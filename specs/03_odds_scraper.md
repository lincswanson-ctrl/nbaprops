# Spec: NBA Player Props Odds Scraper + Loader (Free / Direct Scrape)

**Module:** `data-pipeline/scrapers/odds/` and `data-pipeline/loaders/odds.py`
**Owner:** Antigravity (autonomous build, Claude reviews PR)
**Depends on:** `specs/01_nba_stats_scraper.md` and `specs/02_injury_scraper.md`
**Estimated effort:** 2-3 days end-to-end
**Cost:** $0/month — direct scrape from sportsbook public web/JSON endpoints, no third-party API

---

## Goal

Capture NBA player prop lines directly from DraftKings, FanDuel, and BetMGM throughout each game day, plus dedicated closing-line capture. Without odds data we have no way to identify edges, no way to backtest, and no way to measure performance. This is the most important data feed in the system.

We are not using The Odds API or any other paid aggregator. Each sportsbook exposes the same JSON data their own web app uses; we scrape it directly. This is more code to maintain (sportsbooks occasionally change their internal endpoints) but $0/mo and gives us full control.

## Cost-Free Strategy

| Book | Method | Notes |
|---|---|---|
| **DraftKings** (primary, where we'd actually bet) | Reverse-engineered JSON endpoints | Public, no auth required. Stable for years. |
| **FanDuel** (line shopping) | Reverse-engineered JSON endpoints | Public, no auth. |
| **BetMGM** (line shopping) | Reverse-engineered JSON endpoints | Public, no auth. May require Cloudflare bypass via Playwright. |
| **Sharp reference** | DraftKings closing line | Pinnacle is the gold standard but not legally available in the US without paid aggregator. DK closing line is workable; we can revisit if/when we pay for Pinnacle access. |

**No paid APIs. No login required. No financial commitment to start.**

## Why This Is Hard

Sportsbooks don't publish their JSON endpoint URLs and they aren't documented. We discover them by opening the public sportsbook page in a browser, watching the Network tab, and identifying the XHR/fetch requests that populate the page. Antigravity can do this end-to-end with its browser tool: navigate to the site, intercept network traffic, find the right requests, document the endpoints, then build a scraper that hits those endpoints directly.

Bookmakers occasionally change endpoint paths or response schemas. We mitigate by:
1. Saving raw responses before any parsing — when a parser breaks, we can replay against history.
2. Schema validation that fails loud — silent drift is worse than crashes.
3. Cross-source sanity checks — if DK lines suddenly disagree with FD by huge margins, alert.
4. Keeping a Playwright fallback for when JSON endpoints get locked down behind bot detection.

## Success Criteria

1. Running `python -m data_pipeline.scrapers.odds --book draftkings --markets points --once` fetches today's NBA player points props from DK and saves raw JSON to storage.
2. Same for `--book fanduel` and `--book betmgm`.
3. Running `python -m data_pipeline.loaders.odds` reads raw responses and upserts to `prop_lines` and `closing_lines` tables.
4. Closing lines are captured automatically within 60 seconds before tipoff for every prop we polled during the day.
5. Vig removal and edge calculation utilities exist and are unit-tested with known inputs.
6. Player name resolution succeeds for ≥99% of props; unresolved names route to `unresolved_player_names` (table from spec 02).
7. Game ID resolution (sportsbook event ID → our `games.game_id`) succeeds for 100% of NBA games.
8. Cross-source validator runs nightly and flags any (game, player, market) where DK and FD lines differ by more than 1.5 points or 15 cents in price (likely indicates a stale parser).
9. All tests pass.

## Endpoint Discovery (Required First Step)

Before writing scrapers, Antigravity must use its browser tool to discover the current JSON endpoints for each sportsbook. The endpoints have been roughly stable but exact URLs and IDs change. Document everything found in `data-pipeline/scrapers/odds/discovered_endpoints.md`.

### DraftKings Discovery Procedure

1. Open `https://sportsbook.draftkings.com/leagues/basketball/nba` in browser tool.
2. Open Network tab, filter to XHR/Fetch.
3. Click the "Player Props" or "Stats" tab on a game.
4. Identify the JSON request that returns player prop data. Historically this is something like:
   - Event group list: `https://sportsbook.draftkings.com//sites/US-SB/api/v5/eventgroups/42648?format=json` (42648 = NBA event group ID)
   - Player props per event: `.../eventgroups/{event_group_id}/categories/{category_id}/subcategories/{subcategory_id}`
   - Category IDs of interest: Player Points, Player Rebounds, Player Assists, Player Threes, etc. — discover IDs.
5. Document:
   - Base URL pattern
   - Required headers (User-Agent, Accept-Language, Referer)
   - NBA event group ID
   - All category/subcategory IDs for the markets we want
   - Response schema (key paths to player name, line, prices, market name)

### FanDuel Discovery Procedure

1. Open `https://sportsbook.fanduel.com/navigation/nba` in browser.
2. FanDuel's API usually lives at `https://sbapi.<state>.sportsbook.fanduel.com/api/...` — note the state-specific subdomain (e.g., `sbapi.nj.sportsbook.fanduel.com`). Use whichever state Lincoln's IP geolocates to (California → may need a generic non-state subdomain or Playwright route).
3. Find the request that returns event lists, then the request that returns player props per event.
4. Document base URL, headers, schema.

### BetMGM Discovery Procedure

1. Open `https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004` (or current path).
2. BetMGM's API endpoints typically live at `https://sports.<state>.betmgm.com/cds-api/...`.
3. Find the events and player props endpoints.
4. Document. **BetMGM has the most aggressive bot detection of the three** — Cloudflare may block direct httpx requests. Default to Playwright for BetMGM scrapes if needed.

### Acceptance for Discovery Step

Before writing any scraper code, Antigravity submits a small PR with `discovered_endpoints.md` containing all discovered endpoints and a one-page sample raw response from each book (saved as a fixture). Lincoln reviews this before scraper code is written. **Do not skip this step.**

## Database Schema (Migration `003_odds.sql`)

The `prop_lines` and `closing_lines` tables are declared in `ARCHITECTURE.md` section 5. This migration adds:

```sql
ALTER TABLE prop_lines ADD COLUMN bookmaker_event_id TEXT;
ALTER TABLE prop_lines ADD COLUMN bookmaker_key TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE prop_lines ADD COLUMN raw_response_path TEXT;

ALTER TABLE closing_lines ADD COLUMN captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE closing_lines ADD COLUMN minutes_before_tip REAL;

CREATE INDEX idx_prop_lines_game_player ON prop_lines(game_id, player_id);
CREATE INDEX idx_prop_lines_recent ON prop_lines(snapshot_ts DESC);
CREATE INDEX idx_prop_lines_market_book ON prop_lines(market, book);
CREATE INDEX idx_closing_lines_game ON closing_lines(game_id);

-- Map sportsbook event IDs to our game_id
CREATE TABLE bookmaker_event_mapping (
    bookmaker_key TEXT NOT NULL,
    bookmaker_event_id TEXT NOT NULL,
    game_id TEXT NOT NULL REFERENCES games(game_id),
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    commence_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (bookmaker_key, bookmaker_event_id)
);

-- Track scraper health per book
CREATE TABLE scraper_run_log (
    id BIGSERIAL PRIMARY KEY,
    bookmaker_key TEXT NOT NULL,
    market TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    events_attempted INT DEFAULT 0,
    events_succeeded INT DEFAULT 0,
    props_inserted INT DEFAULT 0,
    error_message TEXT,
    raw_response_paths TEXT[]
);
```

## File Structure

```
data-pipeline/
├── scrapers/
│   ├── odds/
│   │   ├── __init__.py
│   │   ├── base.py                    # Common Pydantic models, interfaces
│   │   ├── draftkings.py
│   │   ├── fanduel.py
│   │   ├── betmgm.py
│   │   ├── discovered_endpoints.md    # Documentation of all discovered URLs
│   │   └── __main__.py                # CLI dispatcher
├── loaders/
│   ├── odds.py
│   └── event_resolution.py            # bookmaker event_id -> games.game_id
├── schemas/
│   └── 003_odds.sql
betting/
├── vig.py                             # vig removal utilities
└── (edge.py, sizing.py come in later specs)
tests/
├── fixtures/
│   └── odds/
│       ├── draftkings/
│       │   ├── eventgroup_sample.json
│       │   ├── player_points_sample.json
│       │   └── player_rebounds_sample.json
│       ├── fanduel/
│       │   ├── events_sample.json
│       │   └── player_props_sample.json
│       └── betmgm/
│           └── ...
├── unit/
│   ├── scrapers/odds/
│   │   ├── test_draftkings.py
│   │   ├── test_fanduel.py
│   │   └── test_betmgm.py
│   ├── loaders/
│   │   ├── test_odds_loader.py
│   │   └── test_event_resolution.py
│   └── betting/
│       └── test_vig.py
└── integration/
    └── test_odds_e2e.py
```

## Implementation Requirements

### Common Models (`scrapers/odds/base.py`)

```python
class PropOffer(BaseModel):
    bookmaker_key: str          # 'draftkings' | 'fanduel' | 'betmgm'
    bookmaker_event_id: str     # whatever the book uses internally
    home_team_name: str         # raw team name from book
    away_team_name: str
    commence_time: datetime     # UTC
    raw_player_name: str
    market: str                 # normalized: 'player_points', 'player_rebounds', etc.
    line: float
    over_price: int             # American odds
    under_price: int
    last_update: datetime | None
    scraped_at: datetime

class OddsScraper(Protocol):
    bookmaker_key: str
    async def list_events(self) -> list[Event]: ...
    async def fetch_player_props(self, event: Event, market: str) -> list[PropOffer]: ...
```

### Per-Book Scrapers

Each book gets its own module implementing the `OddsScraper` interface. Common patterns across all three:

- Use `httpx.AsyncClient` with browser-like headers.
- If a request returns suspicious content (Cloudflare challenge HTML, JSON error mentioning bot detection), automatically retry with Playwright.
- Save every raw response to `<root>/raw/odds/<book>/<UTC_date>/<event_id>_<market>_<UTC_timestamp>.json` BEFORE parsing.
- Implement exponential backoff with jitter on 429 and 5xx.
- Log every fetch via `structlog`: book, event_id, market, status, latency, response_size.
- Hard-code no magic numbers — discovered endpoint IDs go in a constants file, configurable via env vars for rapid response when sites change.

### CLI Dispatcher (`scrapers/odds/__main__.py`)

```
python -m data_pipeline.scrapers.odds --book draftkings --markets points --once
python -m data_pipeline.scrapers.odds --book all --markets all --watch
python -m data_pipeline.scrapers.odds --closing-lines      # special closing-line mode
python -m data_pipeline.scrapers.odds --backfill --book draftkings --date 2025-11-15
```

Polling cadence (default `--watch`):

- Every 10 minutes from 4 hours before earliest tipoff to 30 minutes before earliest tipoff.
- Every 1 minute from 30 minutes before tipoff to 1 minute before tipoff. **This is the closing-line capture window.**

Cadence is conservative (10 min instead of every few seconds) because we're scraping public endpoints — being polite reduces risk of getting blocked.

### Closing Line Capture

For each game starting in the next 5 minutes, poll every 30 seconds. Capture the **last** snapshot before `commence_time` for each (game, player, market, book). Insert into `closing_lines` with `minutes_before_tip = (commence_time - snapshot_ts) / 60`.

Best-case capture: 30-90 seconds before tipoff. Acceptable: <5 minutes. Failed: >5 minutes — log warning to Discord.

### Loader (`loaders/odds.py`)

For each prop offer:
1. Resolve `(home_team_name, away_team_name, commence_time)` → `game_id` via `event_resolution.py` (cached in `bookmaker_event_mapping`).
2. Resolve `raw_player_name` → `player_id` via `name_resolution.py` from spec 02.
3. Normalize market key (each book uses different strings — DK might say "Player Points O/U", FD says "Points", we normalize to `player_points`).
4. Upsert into `prop_lines` keyed on `(snapshot_ts, game_id, player_id, market, book)`.

Special case for closing lines: upsert into `closing_lines` keyed on `(game_id, player_id, market, book)`.

Wrap each snapshot in a transaction.

### Event Resolution (`loaders/event_resolution.py`)

Each book gives event data with home/away team names + commence_time. Match to our `games.game_id`:

1. Normalize team names to abbreviations (DK uses full names like "Boston Celtics" → "BOS", FD uses similar, BetMGM may use shortened forms).
2. Build a team-name alias dictionary at first run; persist to a `team_name_aliases` table.
3. Look up `games` for date range matching `commence_time ± 24h` with home and away teams matching.
4. Cache mapping in `bookmaker_event_mapping` to avoid re-resolving on every snapshot.

### Vig Removal (`betting/vig.py`)

Books bake an overround into prop prices. To get the book's true implied probability, remove the vig.

```python
def american_to_implied_prob(american_odds: int) -> float: ...
def implied_prob_to_american(prob: float) -> int: ...

def vig_remove_proportional(over_price: int, under_price: int) -> tuple[float, float]:
    """Simple proportional method. Slightly biased toward favorites."""
    p_over_raw = american_to_implied_prob(over_price)
    p_under_raw = american_to_implied_prob(under_price)
    total = p_over_raw + p_under_raw
    return p_over_raw / total, p_under_raw / total

def vig_remove_power(over_price: int, under_price: int) -> tuple[float, float]:
    """Power method (Shin's model approximation). More accurate at extremes.
    Find c such that p_over^c + p_under^c = 1. Solve via bisection."""
    ...
```

Default to power method in production code. Both methods are pure math — must be exact and unit-tested.

## Test Cases (Required)

### Unit Tests

1. `test_american_to_implied_prob` — known values: -110 → 0.5238, +100 → 0.5, +150 → 0.4, -200 → 0.6667.
2. `test_implied_prob_roundtrip` — convert American → implied → American, verify identity (within rounding).
3. `test_vig_remove_proportional_balanced` — -110/-110 → 0.5/0.5.
4. `test_vig_remove_proportional_unbalanced` — -150/+130 → known values from manual calc.
5. `test_vig_remove_power_balanced` — same.
6. `test_vig_remove_power_extreme` — -500/+350, verify favorite probability is closer to true than proportional method.
7. `test_dk_parse_eventgroup` — fixture parses correctly.
8. `test_dk_parse_player_points_market` — fixture with multiple players, multiple lines parses correctly.
9. `test_dk_parse_handles_alt_lines` — same player with multiple lines (e.g., 22.5 and 24.5) parses to multiple offers.
10. `test_fd_parse_player_props` — fixture parses correctly.
11. `test_betmgm_parse_player_props` — fixture parses correctly.
12. `test_market_normalization` — each book's raw market strings normalize to canonical keys.
13. `test_team_name_normalization` — "LA Lakers", "Los Angeles Lakers", "Lakers" all → LAL.
14. `test_event_resolution_exact_match` — "Boston Celtics" @ "Lakers" on 2025-01-15 → correct game_id.
15. `test_event_resolution_handles_la_clippers_lakers` — disambiguate same-arena LAL vs LAC.
16. `test_event_resolution_unmatched_logs_warning` — unknown team logs and skips.
17. `test_loader_idempotent` — load same snapshot twice, row count unchanged.
18. `test_loader_inserts_new_snapshot_at_new_timestamp` — same prop at different times = two rows.
19. `test_closing_line_picks_latest_before_tipoff` — given a sequence of snapshots, the latest before tipoff is captured.
20. `test_closing_line_skips_post_tipoff` — snapshot after tipoff not used.
21. `test_scraper_falls_back_to_playwright_on_cloudflare` — mock cloudflare-blocked response triggers Playwright path.
22. `test_scraper_saves_raw_before_parsing_failure` — when parser raises, raw file still on disk.

### Integration Tests

23. `test_e2e_dk_today` — scrape live DK NBA props, verify nonzero rows in `prop_lines`. Network test, marked `@pytest.mark.network`.
24. `test_e2e_fd_today` — same for FanDuel.
25. `test_e2e_betmgm_today` — same for BetMGM.
26. `test_e2e_closing_line_simulated` — simulate game with synthetic snapshots, verify closing line picked correctly.
27. `test_cross_source_agreement` — fetch all three books at same timestamp, compare lines for identical props. Mean line difference should be < 1 point on points markets.

## Cross-Source Validator

`scripts/validate_odds_sources.py` runs nightly:

```
Validating odds sources at 2025-11-15T22:00:00Z

Total props with multi-book coverage: 312
DK present: 312
FD present: 298 (95.5%)
BetMGM present: 287 (92.0%)

Mean line difference (DK vs FD): 0.31 points
Mean price difference (DK vs FD): 4.2 cents
Mean line difference (DK vs MGM): 0.42 points

Disagreements > 1.5 line points: 7 (likely parser issues)
  - LeBron James points: DK=25.5, FD=22.5 (3.0 diff) — INVESTIGATE
  - [...]
```

Disagreements above the threshold get flagged to Discord — they almost always indicate a parser or schema-drift issue.

## Closing Line Audit

`scripts/audit_closing_lines.py` runs nightly:

- Games yesterday: N
- Closing lines captured: M (target: ≥99% of N × markets × books)
- Mean `minutes_before_tip` for captures: X (target: < 2)
- Games missing closing lines: list with reasons (book down? tipoff time wrong? parser failure?)

## Operational Considerations

- **Sportsbook endpoint changes**: when a parser breaks, the raw_response_path on every recent snapshot lets us replay against history once we fix the parser. We never lose data we successfully fetched.
- **IP / geolocation**: sportsbooks may serve different content based on location. From California, DK and FD work without VPN; BetMGM may serve a "this state not supported" page that needs filtering. Handle gracefully.
- **Bot detection**: if any book starts blocking, the Playwright fallback handles it. If Playwright also fails, log loudly and continue with the other books — partial data beats no data.
- **No rate limit specified by books, so be polite**: 10-min default polling, never more than 1 request per second to a single book.

## Markets to Track (Phase 1 = points only, but spec all so we don't refactor)

```
player_points
player_rebounds
player_assists
player_threes
player_points_rebounds_assists  (PRA)
player_points_rebounds          (P+R)
player_points_assists           (P+A)
player_rebounds_assists         (R+A)
player_steals
player_blocks
player_steals_blocks
player_turnovers
```

Each book uses different raw market names — the per-book scraper does the mapping. Document the mapping in each scraper module's constants.

## Out of Scope

- Live in-game odds (we don't bet live).
- Sportsbook account integration / actual bet placement (separate spec, possibly never).
- Alternative lines (e.g., over 25.5 vs over 24.5 same player) — we'll add this later since it requires multi-line distribution modeling. **Exception:** capture them in raw scrape if returned, but Phase 1 model only uses the main line.
- Same-game parlays.
- Futures and team props.
- Historical odds backfill — no good free source. We'll start collecting now and accumulate our own history.

## A Note on Historical Odds

Direct sportsbook scraping does **not** give us historical data — we only get what's live now. This means our backtest is limited to data we collect from this point forward. Two implications:

1. **Initial backtest is forward-only.** Build the pipeline now, let it collect for 4-6 weeks, then start backtesting on accumulated data.
2. **Model can still be evaluated without historical odds.** We can still measure prediction MAE against actual game outcomes using the historical box score data from spec 01. We just can't measure historical CLV or simulated bet profitability until we've accumulated odds history.

This is the main tradeoff of the free path. If the model proves promising on prediction accuracy, paying for historical odds becomes a justified expense later.

## Deliverables

Submit two PRs for this spec:

**PR 03a — Endpoint Discovery (small, fast):**

1. `discovered_endpoints.md` documenting all URLs, IDs, headers, schemas for DK, FD, BetMGM.
2. Sample raw response fixtures (one event group, one player props request per book).
3. Notes on which books needed Playwright vs which work with httpx.

Lincoln reviews and approves before PR 03b.

**PR 03b — Full Implementation:**

1. Confirmation of each success criterion with verification snippets.
2. Pytest output showing all tests passing.
3. Sample of `prop_lines` after a real polling cycle (one game, one market, all three books).
4. Output of vig removal on 5 real prop offers, hand-verified.
5. Output of cross-source validator on a real run.
6. Estimated daily request count per book at default polling cadence.
7. Any deviations from spec, with justification.

Stop and ask Lincoln if anything is unclear before writing code.
