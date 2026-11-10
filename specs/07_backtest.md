# Spec: Walk-Forward Backtest

**Module:** `orchestration/backtest.py` (DB reads + CLI), pure helpers in
`models/walk_forward.py`, `models/evaluation.py`, `betting/grading.py`, `betting/selection.py`
**Owner:** Claude (Lincoln approves)
**Depends on:** specs 01 (box scores), 03 (`closing_lines`), 05 (baseline_v1), 06 (two-sided edges)
**Status:** Draft — implemented; open decisions at the bottom

---

## Goal

Measure whether the model has an edge **before** it's used for betting, using only
information available before each game (CLAUDE.md: never train on data after the
target's game date).

## Modes

### 1. Calibration (box scores only — works for any backfilled season)

For every regular-season player-game in the range and each market in
`CALIBRATION_MARKETS` (points, rebounds, assists, threes, PRA):

- History = the player's played games strictly before the game date
  (`HistoryIndex.prior_played`, same window as baseline_v1).
- **Proxy line** = `floor(last-10 simple average) + 0.5`, standing in for where books set
  lines; also scored at proxy ± 1.
- Record model P(over) and whether the player went over.

Report per market: samples, base rate, Brier score, Brier skill vs base rate, log loss,
mean absolute error of the projected mean, 10-bucket reliability table.

### 2. Betting (needs stored closing lines)

- Per `(game, player, market)`: consensus from `closing_lines` (spec 06), model P(over) at
  the consensus line, side via `pick_side`.
- Per game date: **the same `select_picks` + `selection_rules()` the live pregame step
  uses** (thresholds, per-player/per-game caps). `pregame.run_edge_selection` was refactored
  to call the shared function; a fake-data run produced identical edges and picks before and
  after the refactor.
- Grade each pick against the box score: win / loss / push (whole-number line hit
  exactly) / void (player did not play). Flat 1-unit stakes at the pick's price.
- Report: bets, W/L/P/void, win rate, profit, ROI (voids excluded from units risked),
  average edge and average model probability, broken down by market, side and edge bucket;
  daily bankroll curve from 100 units; per-bet CSV.

Closing lines are the sharpest available price, so betting results here are a
**conservative** estimate versus betting morning lines.

## Scope Rules

- Regular season only by default (NBA game ids starting `002`); `--all-game-types`
  includes playoffs/play-in (needed while stored lines are mostly 2026 playoffs).
- Read-only against Postgres; outputs go to `data/backtest/` (`calibration_<label>.json`,
  `summary_<label>.json`, `bankroll_<label>.json`, `bets_<label>.csv`). Box scores are
  streamed with a server-side cursor.

## CLI

```
python -m orchestration.backtest --season 2025-26 --mode calibration
python -m orchestration.backtest --start 2026-04-18 --end 2026-06-20 --mode betting \
    --all-game-types --label playoffs-2026
```

## Verification (fake data, 2026-11-10)

| Fake book | Bets | Win rate | ROI | Avg claimed edge |
|---|---|---|---|---|
| Lines = last-10 average (noisy) | 540 | 70.0% | +33.6% | 12.9% |
| Lines = true mean (perfect) | 540 | 53.4% | +1.9% | 17.9% |

- No leakage: against a perfect book the result is near break-even (52.4% at −110); unit
  test `test_build_candidates_never_sees_target_game` guards it directly.
- Calibration at proxy lines was good on fake data (reliability buckets within ~2 points).

## Finding: claimed edges are mostly estimation noise

Against a perfect market, baseline_v1 still claimed an average 17.9% edge on its picks
(average model probability ~0.68 vs 53% realized). A 20-game projection has sampling
error, and with no shrinkage toward the market every large disagreement looks like an edge.
**Real books are far sharper than a 10-game average, so on real lines most current picks
will be noise.** Next step (proposed spec 08): blend the model with the de-vigged market
probability (`p = w·p_model + (1−w)·p_market`), fit `w` per market on backtest data, and
re-derive thresholds from realized ROI by edge bucket.

## Open Decisions for Lincoln

1. Proxy line = last-10 average. Alternatives: last-5, or season-to-date.
2. Flat 1-unit staking only; Kelly sizing (`betting/sizing.py` in ARCHITECTURE) later?
3. ~~Placeholder `data/backtest/*_2024-25.json`~~ — deleted 2026-11-10 (they were not real
   results).
