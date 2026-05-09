# Spec: NBA Stats Scraper + Loader

**Module:** `data-pipeline/scrapers/nba_stats.py` and `data-pipeline/loaders/nba_stats.py`
**Owner:** Antigravity (autonomous build, Claude reviews PR)
**Depends on:** `ARCHITECTURE.md` schema section
**Estimated effort:** 1 day end-to-end

---

## Goal

Build a scraper for stats.nba.com that pulls all data needed for Phase 1 (player points props), plus a loader that normalizes raw responses into the Postgres tables defined in `ARCHITECTURE.md`. Idempotent, tested, production-ready.

## Success Criteria

You are done when all of these are true:

1. Running `python -m data_pipeline.scrapers.nba_stats --season 2024-25` populates raw responses to `s3://prop-model/raw/nba_stats/<endpoint>/<date>/*.json` (or local equivalent at `./data/raw/nba_stats/...` for dev).
2. Running `python -m data_pipeline.loaders.nba_stats --season 2024-25` reads those raw files and upserts into `players`, `teams`, `games`, `player_game_stats`, and `lineups` tables.
3. Re-running either step produces zero net changes (idempotency verified by row count + checksum test).
4. Pytest suite passes including: schema validation, idempotency test, missing-data handling, malformed-response handling.
5. A backfill from 2020-21 season to current completes without manual intervention and produces ≥99% row coverage vs Basketball Reference (validation script included).

## Endpoints Required

Use these stats.nba.com endpoints. Required headers are listed at the bottom of this spec.

| Endpoint | Purpose | Frequency |
|---|---|---|
| `leaguegamefinder` | Master list of games per season | Daily |
| `boxscoretraditionalv2` | Per-game player box scores | Per game, after final |
| `boxscoreadvancedv2` | Usage rate, true shooting, pace | Per game, after final |
| `boxscoreplayertrackv2` | Touches, drives, contested shots | Per game, after final |
| `playergamelogs` | Backfill convenience endpoint | One-shot per season |
| `commonplayerinfo` | Player metadata (height, weight, position) | Once per player, refresh weekly |
| `leaguedashplayerstats` | Season-aggregate stats for sanity checks | Daily |
| `boxscorematchupsv3` | Defensive matchup data | Per game |

## Data Flow

```
[NBA API] --GET--> [Raw JSON to storage] --transform--> [Pydantic models] --upsert--> [Postgres]
```

Two distinct stages. Never skip the raw storage stage — it's your replay buffer when the loader has bugs.

## File Structure

```
data-pipeline/
├── scrapers/
│   ├── __init__.py
│   ├── nba_stats.py          # main scraper module
│   └── nba_stats_endpoints.py # endpoint definitions, params, response keys
├── loaders/
│   ├── __init__.py
│   └── nba_stats.py          # raw -> postgres
├── schemas/
│   └── 001_initial_nba.sql   # creates tables from ARCHITECTURE.md section 5
└── db.py                      # connection pool
tests/
├── fixtures/
│   └── nba_stats/
│       ├── boxscore_traditional_sample.json
│       ├── boxscore_advanced_sample.json
│       └── ... (one per endpoint)
├── unit/
│   ├── scrapers/test_nba_stats.py
│   └── loaders/test_nba_stats.py
└── integration/
    └── test_nba_stats_e2e.py
```

## Implementation Requirements

### Scraper (`scrapers/nba_stats.py`)

- Class `NBAStatsScraper` with one method per endpoint, e.g. `fetch_boxscore_traditional(game_id: str) -> dict`.
- Use `httpx` with `httpx.AsyncClient` for concurrent fetches; cap concurrency at 5 to avoid rate limits.
- Implement exponential backoff with jitter on 429 and 5xx (use `tenacity`).
- Every response is saved to raw storage **before** any parsing. Path: `<root>/raw/nba_stats/<endpoint>/<season>/<game_id_or_date>.json`.
- Log every request: endpoint, params, status code, response size, latency.
- CLI entrypoint accepts `--season`, `--start-date`, `--end-date`, `--endpoint` (optional, defaults to all).
- Concurrency: process games in parallel but respect a global rate limit of 30 requests/minute.
- Configurable via env vars: `NBA_STATS_RATE_LIMIT_PER_MIN`, `RAW_STORAGE_PATH`.

### Loader (`loaders/nba_stats.py`)

- Class `NBAStatsLoader` with one method per target table.
- Reads raw JSON from storage, validates with Pydantic models, upserts to Postgres.
- Pydantic models live in `loaders/models.py`. Strict validation — fail loud on schema drift.
- Use `psycopg[binary]` with `executemany` for batch upserts.
- All upserts use `INSERT ... ON CONFLICT (natural_key) DO UPDATE SET ...`.
- Wrap each game's load in a transaction. If any table fails for a game, roll back that game and log it; do not abort the whole batch.
- CLI entrypoint accepts `--season`, `--game-id`, `--reload-all`.
- Track loaded games in a `load_log` table (game_id, endpoint, loaded_at, raw_file_checksum) so you can detect already-loaded games and skip.

### Schema Migration (`schemas/001_initial_nba.sql`)

Implement the tables from `ARCHITECTURE.md` section 5. Add these specifics not covered there:

```sql
-- Add to all tables: created_at, updated_at TIMESTAMPTZ DEFAULT NOW()
-- Indexes:
CREATE INDEX idx_player_game_stats_player_date ON player_game_stats(player_id, game_id);
CREATE INDEX idx_player_game_stats_game ON player_game_stats(game_id);
CREATE INDEX idx_games_date ON games(date);
CREATE INDEX idx_games_season ON games(season);
CREATE INDEX idx_injury_status_player_date ON injury_status(player_id, status_date DESC);

-- Load tracking
CREATE TABLE load_log (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    natural_key TEXT NOT NULL,
    raw_file_path TEXT NOT NULL,
    raw_file_checksum TEXT NOT NULL,
    loaded_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source, endpoint, natural_key)
);
```

### Required Headers for stats.nba.com

```python
NBA_STATS_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}
```

If you get blocked despite these headers, switch to using a Playwright browser session via Antigravity's browser tool to maintain a real session. Document any header changes you needed to make in the PR.

## Test Cases (Required)

### Unit Tests

1. `test_parse_boxscore_traditional_full_game` — happy path, all fields present.
2. `test_parse_boxscore_handles_dnp` — player with `DNP - Coach's Decision`, minutes=0, stats=NULL.
3. `test_parse_boxscore_handles_overtime` — game with 2OT, periods reflected correctly.
4. `test_loader_idempotent` — load same game twice, verify row counts unchanged after second load.
5. `test_loader_handles_traded_player` — player appears for two teams in same season; both records preserved.
6. `test_loader_rejects_malformed_response` — missing required field raises ValidationError, no DB writes happen.
7. `test_scraper_retries_on_429` — mock 429 then 200, verify retry happened with backoff.
8. `test_scraper_writes_raw_before_parse` — verify raw file exists even if parsing later fails.

### Integration Tests

9. `test_e2e_single_game` — scrape one real game, load it, verify all 4 target tables populated correctly.
10. `test_e2e_full_day` — scrape a full slate (10+ games), verify all loaded.
11. `test_backfill_validation` — pull a single past season, compare player season totals (PPG, MPG, GP) to a known-good fixture from Basketball Reference. Tolerance: 1% on counting stats, exact on game count.

## Validation Script

Build `scripts/validate_nba_load.py` that:

1. Picks 20 random games from the loaded data.
2. For each, queries `player_game_stats` for box score and reconstructs the team total.
3. Compares to the `games` table home/away score.
4. Flags any mismatch with player names + game ID for human review.

A successful Phase 1 backfill must pass with zero mismatches.

## Out of Scope (Do Not Build)

- Injury data — separate spec.
- Odds data — separate spec.
- Referee data — separate spec.
- Real-time/live game updates — separate spec.
- Lineup-level on/off splits — extract from `lineups` table later in feature engineering.

## Deliverables

When you submit the PR, include in the description:

1. Confirmation each success criterion is met (with verification command run + output snippet).
2. List of test cases passing.
3. Output of the validation script on a real backfill.
4. Any deviations from this spec, with justification.
5. Any new top-level dependencies added (must also be added to ARCHITECTURE.md).

If anything in this spec is unclear, stop and ask Lincoln before writing code.
