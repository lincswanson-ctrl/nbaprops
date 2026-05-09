# Prop Model Architecture

**Owner:** Lincoln
**Status:** Active build
**Scope:** NBA only. All NBA player props (points, rebounds, assists, threes, PRA, steals+blocks, etc.).
**Phase 1 prop in scope:** Player points
**Expansion path within NBA:** points → rebounds → assists → threes → combo props (PRA, P+R, P+A)

This project is NBA-only. Do not add code, schemas, or abstractions for other sports. If a generalization "for other sports later" is tempting, resist — it adds surface area for bugs and slows the build. We will not be modeling NFL, MLB, or any other league in this repo.

---

## 1. System Overview

A daily-cycle pipeline that ingests NBA data, projects player point distributions, compares to live sportsbook lines, and surfaces +EV bets ranked by edge.

```
[Scrapers] -> [Raw S3/local] -> [Postgres] -> [Feature Store] -> [Model] -> [Predictions]
                                                                                |
                                                                                v
                                                              [Live Odds] -> [Edge Engine] -> [Bet Log] -> [Dashboard]
```

Daily cycle runs at 9am PT (after injury reports start landing) and again at 4pm PT (closer to tipoff for late scratches).

## 2. Tech Stack (Locked)

- **Language:** Python 3.11
- **Database:** Postgres 16 (Supabase for managed hosting)
- **Object storage:** Supabase Storage for raw scrape dumps
- **Orchestration:** Prefect 2 (or plain cron + Python for v1)
- **Modeling:** scikit-learn, XGBoost, LightGBM, statsmodels for quantile regression
- **Dashboard:** React + Supabase (reuse Apex Epoxy patterns)
- **Deployment:** Railway or Fly.io for the worker, Vercel for dashboard
- **Notifications:** Discord webhook for +EV alerts

Do not introduce new dependencies without updating this doc.

## 3. Repo Layout

```
/data-pipeline
  /scrapers       # one file per source
  /loaders        # raw -> Postgres normalizers
  /schemas        # SQL migrations
/features
  /groups         # one file per feature group
  feature_store.py
/models
  /baseline
  /xgb
  /quantile
  train.py
  predict.py
/betting
  vig.py
  edge.py
  sizing.py
  filters.py
/dashboard        # React app
/orchestration
  daily_cycle.py
  backtest.py
/tests
/specs            # agent input specs - one per module
CLAUDE.md
AGENTS.md
ARCHITECTURE.md   # this file
```

## 4. Data Sources (Phase 1)

All data sources are free. No paid APIs.

| Source | Purpose | Method | Update freq |
|---|---|---|---|
| nba.com/stats | Game logs, advanced stats, lineups | Public API (with browser headers) | Daily post-games |
| Basketball Reference | Backfill, advanced metrics | Scrape | Weekly |
| Rotowire injury feed | Injury status, projected lineups | Scrape | Hourly on game days |
| ESPN injuries | Cross-validation of Rotowire | Scrape | 30 min on game days |
| DraftKings | Player props (primary, where we bet) | Reverse-engineered JSON endpoints | 10 min, then 30s near tipoff |
| FanDuel | Player props (line shopping) | Reverse-engineered JSON endpoints | 10 min |
| BetMGM | Player props (line shopping) | Reverse-engineered JSON endpoints | 10 min |
| nba.com/officials | Referee assignments | Scrape | Game day |

## 5. Database Schema (Phase 1 core tables)

```sql
-- Players
players(player_id PK, name, team_id, position, height, weight, birthdate, draft_year)

-- Teams
teams(team_id PK, name, abbreviation, conference, division)

-- Games
games(game_id PK, date, season, home_team_id, away_team_id, home_score, away_score,
      pace, total_possessions, ot_periods, vegas_total, vegas_spread, attendance)

-- Per-game player stats
player_game_stats(player_id, game_id, minutes, points, rebounds, assists, steals, blocks,
                  fga, fgm, fg3a, fg3m, fta, ftm, turnovers, plus_minus, usage_rate,
                  true_shooting, starter BOOL, dnp_reason,
                  PRIMARY KEY (player_id, game_id))

-- Lineup data
lineups(game_id, period, start_seconds, end_seconds, team_id,
        player1_id, player2_id, player3_id, player4_id, player5_id,
        possessions, points_for, points_against)

-- Injury timeline
injury_status(player_id, status_date TIMESTAMP, status, body_part, source,
              PRIMARY KEY (player_id, status_date))

-- Officials
game_officials(game_id, ref1_id, ref2_id, ref3_id)

-- Odds snapshots
prop_lines(snapshot_ts TIMESTAMP, game_id, player_id, market, line, over_price, under_price, book,
           PRIMARY KEY (snapshot_ts, game_id, player_id, market, book))

-- Closing lines (for CLV)
closing_lines(game_id, player_id, market, line, over_price, under_price, book,
              PRIMARY KEY (game_id, player_id, market, book))
```

All timestamps in UTC. All money in American odds format in DB; convert to decimal in code.

## 6. Feature Groups (Phase 1)

Each feature group is its own file in `/features/groups/`. Every feature must be **point-in-time correct** — computed using only data available before tipoff of the target game.

1. **rolling_form** — weighted averages over last 5/10/20 games (points, minutes, usage, TS%, FGA, FG3A, FTA). Recency weights: exponential decay with half-life of 8 games.
2. **opponent_defense** — opp DRtg vs position over last 10 games, opp pace, opp pts allowed to position, opp 3PT% allowed, opp FT rate allowed.
3. **pace_minutes** — projected pace (avg of team pace and opp pace, adjusted for B2B), projected minutes incorporating injuries to teammates and recent minutes trends.
4. **teammate_context** — usage shift when key teammates are out (computed from historical on/off splits). Specifically: top-3 teammates by minutes played together, with usage delta when each is absent.
5. **game_environment** — Vegas total, implied team total (total/2 + spread/2), spread, expected blowout flag (|spread| > 10), home/away.
6. **rest_travel** — days rest, B2B flag, 3-in-4 flag, 4-in-6 flag, travel miles since last game, time zones crossed, altitude (Denver +1, Utah +1).
7. **referee** — ref crew avg fouls per game, FT rate allowed, pace impact (some refs let games breathe, others whistle constantly).
8. **situational** — month of season, days into season, post-allstar flag, last 10 games of regular season flag (rest risk), nationally televised flag.
9. **player_meta** — age, years in league, position, height, role flag (star/starter/rotation/bench based on minutes percentile).

## 7. Modeling Approach

**Target (Phase 1):** Player points (continuous, discrete spikes at common values like 10, 15, 20, 25 due to scoring patterns).

**Why points first:** highest volume market, deepest historical data, most stable distribution shape, easiest to validate. Once the points pipeline is proven, the same architecture extends to rebounds/assists by retraining with different targets.

**Stack:**
1. **Baseline projection** = weighted recent form × opponent adjustment × pace adjustment × minutes adjustment. Closed-form, no ML.
2. **XGBoost residual model** — trained to predict `actual_points - baseline_projection` using all features.
3. **Final point estimate** = baseline + XGB residual prediction.
4. **Distribution model** — separate quantile regression (LightGBM with quantile loss) at q={0.1, 0.25, 0.5, 0.75, 0.9} to estimate the full distribution.
5. **Calibration** — isotonic regression on validation set to fix systematic miscalibration.

**Train/val/test:**
- Walk-forward by date. Never random splits.
- Train on seasons 2021-22 through 2023-24, validate on first half of 2024-25, test on second half.
- Final production model retrains weekly on all data through yesterday.
- Regular season and playoffs are modeled separately (different model artifacts). Phase 1 covers regular season only.

**NBA-specific considerations baked into modeling:**
- Back-to-back games depress minutes for stars (load management) — must be a feature.
- Rotation games near the trade deadline (Feb) often produce blowouts — flag explicitly.
- Late-season tanking and rest behavior in March-April — players sit unexpectedly.
- All-Star break creates a discontinuity — separate before/after features.
- 3-point variance is enormous — points-from-threes have a much fatter tail than points-from-twos. Model handles this via the quantile regression.

**Acceptance criteria for production:**
- Calibration: predicted P(over) within ±3% of actual hit rate across deciles
- CLV: median CLV > 0 across last 200 logged bets at >= 4% modeled edge (using DK closing line as sharp reference; we don't have Pinnacle on the free path)
- MAE on points: < 4.5 (league baseline ~5.5). This can be evaluated immediately on historical box scores without needing accumulated odds data.

## 8. Betting Logic

```
implied_prob_book = vig_remove(over_price, under_price)
modeled_prob = P(points > line | distribution)
edge = modeled_prob - implied_prob_book
```

**Bet rules:**
- Minimum edge: 4.0% (tunable)
- Minimum modeled prob: 50% (no dog hunting until model is proven)
- Maximum bet: 2% of bankroll (quarter-Kelly capped)
- Skip if line moved >0.5 against us between snapshot and bet time
- Skip if injury status for any starter changed in last 30 min (re-run features first)

**Bet logging:** every recommended bet logged with full feature snapshot, predicted distribution, edge, sizing, and outcome. This is your audit trail and your training data improvement loop.

## 9. Monitoring (Dashboard)

Pages:
1. **Today's bets** — sorted by edge, with player name, line, modeled prob, book, recommended size
2. **Bankroll curve** — actual vs expected, with confidence bands
3. **CLV tracking** — per-bet CLV histogram, rolling median
4. **Calibration plots** — predicted vs actual hit rate
5. **Data freshness** — last successful scrape per source, alerts on staleness
6. **Model drift** — rolling MAE, residual distribution

## 10. Hard Rules (Read Before Every Task)

1. **No future leakage.** Features for game G use only data with `timestamp < tipoff(G)`.
2. **No silent failures.** Every scraper, loader, and predictor logs success/failure with row counts.
3. **Idempotent operations.** Re-running the daily cycle must not duplicate data.
4. **Schema migrations only via numbered SQL files** in `/data-pipeline/schemas/`.
5. **Every PR includes tests.** No exceptions.
6. **Every spec change requires updating this file** before code changes.
7. **No new top-level dependencies** without an entry in this doc.
8. **Closing lines are sacred.** Capture them within 1 minute of tipoff for every prop we considered.

## 11. Phase Roadmap (NBA-only, all free)

- **Phase 1 (weeks 1-4):** Build all data pipelines (NBA stats, injuries, odds from DK/FD/MGM). Start collecting odds data — we have no historical odds since we're not paying an aggregator, so we accumulate from this point forward. In parallel, evaluate model on prediction accuracy (MAE on points) using historical box scores from spec 01.
- **Phase 2 (weeks 5-8):** With 4+ weeks of accumulated odds, run first proper backtest. Multi-book line shopping, Discord alerts, paper trading. Goal: validate live execution and confirm modeled edge survives realistic line-shopping.
- **Phase 3 (weeks 9-10):** Add rebounds and assists props using the same model architecture (retrained per stat). Real money at small stakes ($5-25 per bet) split among Lincoln + a couple buddies. Goal: confirm CLV is positive across multiple markets.
- **Phase 4 (weeks 11-12):** Add three-pointers made and combo props (PRA, P+R, P+A). Combo props need joint distribution modeling — separate spec.
- **Phase 5 (weeks 13+):** Dashboard polish, scale stakes proportional to demonstrated CLV. Consider paying for Pinnacle access via The Odds API ($30-119/mo) to get sharper CLV measurement, but only if the free DK closing-line approach proves insufficient.

Per-stat playoff: NBA playoffs change everything (rotations tighten, pace drops, defenses lock in). Playoff model is a separate variant retrained on playoff data only. Add this in Phase 5 if we're still active by then.

Off-season: use down time for model improvements, feature additions, summer league data collection if useful, and prep for the next regular season.
