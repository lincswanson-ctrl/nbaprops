# Spec: Baseline Projection Model (`baseline_v1`)

**Module:** `models/baseline/projection.py` (pure functions) + wiring in `orchestration/pregame.py`
**Owner:** Claude (Lincoln approves)
**Depends on:** `player_game_stats`, `games`, `odds_normalized` (specs 01, 03, `005_pregame_pipeline.sql`)
**Status:** Approved 2026-11-10 with the defaults below (baseline_v1 default, normal approximation, 400-day lookback)

---

## Goal

Replace the placeholder `naive_v1` model with step 1 of the modeling stack in
`ARCHITECTURE.md` §7: a closed-form, no-ML **baseline projection** that turns a player's
recent history into a probability of going **over the specific line** being offered.

## Why

A fake-data smoke test (2026-11-10) showed `naive_v1` is not usable for betting:

- It never reads the line. Probability = market probability + a sum of bonuses, almost all
  non-negative, so **every** market came out as an over with a positive edge (96/96).
- Each player received one edge copied across points, rebounds and assists.
- Example: over 28.5 points on a player averaging 23.9 → "+14.2% edge, B+".

## Scope (this spec)

1. **Stat per market.** Map each market to its box-score stat:

   | Market | Stat |
   |---|---|
   | `player_points` | `points` |
   | `player_rebounds` | `rebounds` |
   | `player_assists` | `assists` |
   | `player_threes` | `fg3m` |
   | `player_steals` / `player_blocks` | `steals` / `blocks` |
   | `player_steals_blocks` | `steals + blocks` |
   | `player_points_rebounds_assists` | `points + rebounds + assists` |
   | `player_points_rebounds` / `player_points_assists` / `player_rebounds_assists` | sums |

   Unknown markets are not scored (logged, skipped).

2. **History window (point-in-time).** The player's most recent `BASELINE_MAX_GAMES = 20`
   played games with `g.date < target_date` and within `BASELINE_LOOKBACK_DAYS = 400` days
   (so early-season projections can use last season). A game counts as played when
   `minutes > 0` and `dnp_reason IS NULL`. Fewer than `BASELINE_MIN_GAMES = 5` → not scored.

3. **Projected mean.** Exponentially weighted mean, most recent game weight 1, half-life
   `BASELINE_HALF_LIFE_GAMES = 8` (matches `rolling_form` in ARCHITECTURE §6).

4. **Spread.** Weighted standard deviation of the same games, floored so that
   variance ≥ mean (box-score counts are at least Poisson-dispersed) and σ ≥ `0.5`.

5. **P(over line).** Normal approximation with continuity correction on an integer stat:
   - `P(over) = 1 − Φ((floor(line) + 0.5 − μ) / σ)`
   - For whole-number lines a push is possible; return `P(over | no push)` =
     `P(X ≥ L+1) / (P(X ≥ L+1) + P(X ≤ L−1))`.
   - Clamp to `[0.01, 0.99]`.

6. **Wiring.** `run_model` writes `model_prob = P(over consensus_line)` and
   `model_version = "baseline_v1"` to `model_scores`. Selected via setting
   `PREGAME_MODEL_VERSION` (`baseline_v1` default, `naive_v1` kept for comparison).

## Out of scope (follow-up specs)

- **Under picks (edge step).** `run_edge_selection` rejects every negative edge because it
  compares the signed edge to a positive threshold, and the odds tables only carry the over
  price (`odds.price ← prop_lines.over_price`). Unders need a new migration adding under
  price/book to `odds` / `odds_normalized`, then two-sided edge selection.
- **Vig removal.** `fair_prob` is the implied probability of the averaged over price alone
  (includes vig, ~52.4% at −110). Needs under prices (above) to de-vig.
- **Mixed-line consensus.** Prices are averaged across books even when their lines differ.
- Opponent, pace and minutes adjustments (rest of ARCHITECTURE §7 step 1), XGBoost
  residual, quantile model, calibration.

## Acceptance Criteria

1. Pure functions in `models/baseline/projection.py`, no I/O, `mypy --strict` clean.
2. Unit tests (≥ 3 cases each: typical, edge, missing data): stat extraction per market,
   weights / weighted mean, spread floor, P(over) for half and whole lines, too-few-games.
3. Symmetry check: line well above the mean → P(over) < 0.5; well below → > 0.5.
4. Fake-data smoke test: with lines set around each player's mean, `model_prob` falls on
   both sides of 0.5 and differs across a player's markets.

## Open Decisions for Lincoln

1. **Default model.** This spec makes `baseline_v1` the default. Keep `naive_v1` as default
   instead until you've reviewed?
2. **Normal approximation** vs negative binomial for low-count stats (steals, blocks,
   threes). Normal is simple and adequate for points/PRA; for counts ≤ 2 it is rough.
3. **Early season.** Using up to 400 days back mixes in last season (role changes, trades).
   Alternative: require ≥ 5 games this season and skip otherwise.
