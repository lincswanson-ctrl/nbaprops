# Spec: Baseline Adjustments — Minutes, Opponent, Pace (`baseline_v2`)

**Module:** `models/baseline/adjustments.py` (pure) + history/context loading in
`orchestration/pregame.py` and `orchestration/backtest.py`
**Owner:** Claude (Lincoln approves)
**Depends on:** specs 01, 02, 05, 07, 08
**Status:** DRAFT ONLY — no code. Build after the real 2025-26 calibration backtest exists,
so every phase can be measured against `baseline_v1`.

---

## Goal

ARCHITECTURE §7 step 1 defines the baseline as
`recent form × opponent adjustment × pace adjustment × minutes adjustment`. `baseline_v1`
(spec 05) implements only recent form. This spec adds the other three, **one phase at a
time, each kept only if it beats the previous version out of sample**.

## Data audit (2026-11-10)

| Input | Status | Consequence |
|---|---|---|
| `player_game_stats.minutes`, box-score counts | Loaded (spec 01) | Minutes model is buildable now |
| `injury_status` (player-linked) | Loaded (spec 02; Rotowire + ESPN fixed this season) | "Teammate out" signal is buildable |
| `games.pace`, `total_possessions` | **Never populated** by the loader | Pace needs a loader change first |
| `player_game_stats.usage_rate` | From `boxscoreadvancedv2`, which returned **empty (2-byte) responses** in the May 2026 go/no-go run | Likely empty; verify before use |
| `players.position` | From `START_POSITION`: bench players have none; overwritten per game | Not usable for "opponent vs position" as-is |
| `games.vegas_spread` / `vegas_total` | **Never populated** | Spread can be derived from `team_moneyline_odds`; total cannot |

## Phases

### Phase A — Minutes (buildable now)

Re-express the projection as **rate × minutes**:

- `rate` = recency-weighted per-minute stat (same weights as v1), over played games.
- `projected_minutes` = recency-weighted minutes, then:
  - **Teammate out:** when a top-3-minutes teammate is `out`/`doubtful` for the target game,
    add that player's historical minutes delta with vs without the teammate (on/off from
    box scores), shrunk toward 0 by sample size. Directly from ARCHITECTURE feature group 4.
  - **Back-to-back:** multiply by the player's own B2B/non-B2B minutes ratio (shrunk to 1).
- Spread scales with projected minutes (variance floor from v1 kept).

### Phase B — Opponent (buildable now, without positions)

- Opponent factor per stat = opponent's **allowed per-minute rate** to players in the same
  **role bucket** (starter / bench by minutes, not position) over its last 15 games, divided
  by the league rate, shrunk toward 1.0 by sample size, capped to [0.85, 1.15].
- Revisit "vs position" only after positions are loaded from a roster endpoint (separate
  spec 01 change).

### Phase C — Pace and game script (blocked)

- Needs: (1) team possessions per game — fix `boxscoreadvancedv2` (or compute possessions
  from team totals, which needs offensive rebounds, not currently stored); (2) spread from
  `team_moneyline_odds` for a blowout-minutes adjustment.
- Spec separately once Phase A/B results are in.

## Measurement (acceptance for each phase)

Using `orchestration.backtest --mode calibration` on the full 2025-26 regular season:

1. Out-of-sample by construction (point-in-time history), compared market by market
   against the previous version on **log loss and MAE of the projected mean**.
2. A phase ships only if it improves log loss on points and PRA **and** does not worsen any
   market by more than noise (paired bootstrap over game dates, 95%).
3. Re-fit spec-08 blend weights after each shipped phase; report betting ROI on the stored
   closing lines as a secondary check (sample is small).

## Out of Scope

- XGBoost residual model, quantile model (ARCHITECTURE §7 steps 2–4).
- Referee, travel and altitude features.

## Open Decisions for Lincoln

1. Order: Phase A (minutes) first, as proposed?
2. Role buckets (starter/bench by minutes) instead of positions for Phase B, until rosters
   are loaded?
3. Should fixing `boxscoreadvancedv2` (empty responses) be prioritized now, since pace and
   usage both depend on it?
