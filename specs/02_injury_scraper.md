# Spec: NBA Injury Status Scraper + Loader

**Module:** `data-pipeline/scrapers/injuries.py` and `data-pipeline/loaders/injuries.py`
**Owner:** Antigravity (autonomous build, Claude reviews PR)
**Depends on:** `specs/01_nba_stats_scraper.md` (needs `players` table populated)
**Estimated effort:** 1-2 days end-to-end

---

## Goal

Build a scraper that captures NBA player injury status throughout the day from multiple sources, plus a loader that maintains a complete time-series history of every status change. Injury timing is the single biggest edge in NBA props — knowing 30 minutes before tipoff that a star is sitting can shift teammate point projections by 5+ points. The system must capture status changes within 5 minutes of them being reported.

## Why Two Sources

We use Rotowire (primary) and ESPN (secondary) as cross-validation. If a player appears OUT on one but ACTIVE on the other, we flag it for human review and use the more pessimistic status. This prevents one site's stale data from poisoning predictions.

## Success Criteria

You are done when all of these are true:

1. Running `python -m data_pipeline.scrapers.injuries --source rotowire` saves raw HTML/JSON to `<root>/raw/injuries/rotowire/<UTC_timestamp>.html`.
2. Running `python -m data_pipeline.scrapers.injuries --source espn` saves raw to `<root>/raw/injuries/espn/<UTC_timestamp>.json`.
3. Running `python -m data_pipeline.loaders.injuries --source rotowire --since 2024-01-01` populates the `injury_status` table with one row per status change per player.
4. The system correctly handles the same player having multiple status changes per day (e.g., "questionable" at 9am → "doubtful" at 2pm → "out" at 6pm) — three rows, not one.
5. Player name matching to `players.player_id` succeeds for ≥99.5% of NBA players. Unmatched names are logged for manual review and a fuzzy-match suggestion table is generated.
6. Pytest suite passes including: parsing fixtures, dedup logic, name-matching edge cases, status escalation tracking.
7. Cross-source validation script runs and outputs disagreement rate; should be <5% on any given snapshot.

## Data Sources

### Rotowire (Primary)

URL: `https://www.rotowire.com/basketball/nba-lineups.php`

Why primary: most timely updates, covers projected starting lineups in addition to injuries, includes "GTD" (game-time decision) and "expected to play" granularity that other sources miss.

Method: HTML scrape. The page is server-rendered, no API. Use `httpx` for fetching and `selectolax` for parsing (faster than BeautifulSoup).

**Browser fallback:** if Cloudflare or bot detection blocks `httpx`, use Antigravity's browser tool with Playwright to render the page in a real browser and capture the HTML. Document which method you settled on.

Update frequency: every 15 minutes during these windows (UTC):
- 14:00–04:00 UTC daily (covers 6am–8pm Pacific, the active window for NBA news)

### ESPN (Secondary / Validator)

URL: `https://www.espn.com/nba/injuries`

Why secondary: well-formatted, less aggressive bot detection, but updates slower than Rotowire. Used to cross-check Rotowire and catch missed updates.

Method: HTML scrape — same approach as Rotowire. ESPN also exposes `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news` for news-feed style updates; use this as a tertiary sanity check if you find the HTML scrape brittle.

Update frequency: every 30 minutes during the active window.

## Status Vocabulary (Normalized)

Different sources use different language. Normalize to this exact set in the DB:

| Normalized | Source language examples |
|---|---|
| `out` | "Out", "OUT", "Inactive", "Will not play" |
| `doubtful` | "Doubtful", "DTD", "Day-to-day (doubtful)" |
| `questionable` | "Questionable", "GTD", "Game-time decision" |
| `probable` | "Probable", "Expected to play" |
| `available` | (no listing, or "Active", "Available") — only logged when transitioning FROM another status |
| `unknown` | source string did not match any known pattern |

Status mapping is in `loaders/injury_status_mapping.py` as a dict, with regex fallbacks. Unmatched strings log `unknown` and write a warning so we can extend the mapping.

## Database Schema (Migration `002_injuries.sql`)

The `injury_status` table is already declared in `ARCHITECTURE.md` section 5. This migration adds:

```sql
-- Track sources separately so we can compare
ALTER TABLE injury_status ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE injury_status ADD COLUMN body_part TEXT;
ALTER TABLE injury_status ADD COLUMN raw_status TEXT NOT NULL;
ALTER TABLE injury_status ADD COLUMN game_id TEXT;  -- if status is for a specific game

-- Drop old PK, add new one that includes source
ALTER TABLE injury_status DROP CONSTRAINT injury_status_pkey;
ALTER TABLE injury_status ADD PRIMARY KEY (player_id, status_date, source);

CREATE INDEX idx_injury_status_recent ON injury_status(status_date DESC);
CREATE INDEX idx_injury_status_player_recent ON injury_status(player_id, status_date DESC);

-- Player name -> player_id resolution table for fuzzy matching
CREATE TABLE player_name_aliases (
    alias TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id),
    source TEXT NOT NULL,
    confirmed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Unresolved names for manual review
CREATE TABLE unresolved_player_names (
    raw_name TEXT NOT NULL,
    source TEXT NOT NULL,
    team_hint TEXT,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    occurrences INT DEFAULT 1,
    suggested_player_id TEXT,
    suggested_confidence REAL,
    PRIMARY KEY (raw_name, source)
);
```

## File Structure

```
data-pipeline/
├── scrapers/
│   ├── injuries.py
│   └── injuries_endpoints.py        # source URLs, parser entrypoints
├── loaders/
│   ├── injuries.py
│   ├── injury_status_mapping.py    # raw string -> normalized status
│   └── name_resolution.py           # fuzzy match raw name -> player_id
├── schemas/
│   └── 002_injuries.sql
tests/
├── fixtures/
│   └── injuries/
│       ├── rotowire_sample.html     # save 3-4 real samples from different days
│       ├── rotowire_partial.html    # mid-day partial update
│       ├── espn_sample.html
│       └── espn_api_sample.json
├── unit/
│   └── scrapers/
│       └── test_injuries.py
│   └── loaders/
│       └── test_injuries.py
└── integration/
    └── test_injuries_e2e.py
```

## Implementation Requirements

### Scraper (`scrapers/injuries.py`)

- Class `RotowireScraper` and `ESPNScraper`, both implementing a common interface:
  ```python
  class InjuryScraper(Protocol):
      async def fetch(self) -> bytes: ...
      def parse(self, content: bytes) -> list[InjuryRecord]: ...
  ```
- `InjuryRecord` is a Pydantic model with fields: `raw_player_name`, `team_abbr`, `raw_status`, `body_part`, `source`, `scraped_at`.
- Every fetch saves the raw response to storage **before** parsing.
- Implement retry with exponential backoff on transient errors.
- Browser fallback: if a normal HTTP fetch returns suspicious content (e.g., Cloudflare challenge page detected by checking for known markers), automatically retry with Playwright.
- CLI accepts `--source {rotowire|espn|all}` and `--once` (single run) or `--watch` (loop with proper interval).
- Logs every run: source, fetched bytes, parsed record count, duration, errors.

### Loader (`loaders/injuries.py`)

- Class `InjuryLoader` with method `load_snapshot(records: list[InjuryRecord]) -> LoadSummary`.
- For each record:
  1. Resolve `raw_player_name + team_abbr` → `player_id` via `name_resolution.py`. If unresolved, write to `unresolved_player_names` and skip the record.
  2. Map `raw_status` → normalized status via `injury_status_mapping.py`. If unmappable, log warning and store as `unknown`.
  3. Look up the player's most recent status from same source. If new status differs (or there's no prior record for today), insert a new row. If identical to most recent same-day record, skip (this is the dedup logic).
- Wrap each snapshot's loads in a transaction.
- Return summary: total records, inserted, deduped, unresolved names, unmapped statuses.

### Name Resolution (`loaders/name_resolution.py`)

NBA player names are messy. "Luka Dončić" appears as "Luka Doncic", "L. Doncic", "Luka Doncić". Build a multi-step resolver:

1. **Exact alias match** — check `player_name_aliases` table.
2. **Normalized exact match** — strip diacritics, lowercase, remove suffixes (Jr., III), match against `players.name`.
3. **Team-scoped fuzzy match** — if team_abbr is known, fuzzy match (rapidfuzz, ratio >= 90) against players currently on that team.
4. **League-wide fuzzy match** — fuzzy match against all active players, ratio >= 95.
5. **Unresolved** — write to `unresolved_player_names` with the highest-scoring suggestion.

When a fuzzy match resolves successfully, write the alias to `player_name_aliases` (unconfirmed) so the next match is O(1). Lincoln will periodically review and confirm aliases via a CLI tool (`scripts/confirm_aliases.py`).

### Cross-Source Validator (`scripts/validate_injury_sources.py`)

For a given timestamp, pull the most recent record from each source for every player and report disagreements. Output:

```
Validating injury sources at 2025-01-15T19:30:00Z

Total players with status: 47
Both sources agree: 44 (93.6%)
Disagreements: 3
  - LeBron James: rotowire=questionable, espn=probable (2h apart)
  - Joel Embiid: rotowire=out, espn=questionable (espn appears stale)
  - Anthony Davis: rotowire=available, espn=questionable

Players in only one source:
  - Rotowire-only (12): [list]
  - ESPN-only (1): [list]
```

A 5%+ disagreement rate consistently indicates a source is broken — alert via Discord.

## Test Cases (Required)

### Unit Tests

1. `test_parse_rotowire_full_slate` — parse fixture with full day's lineups, verify all rows extracted.
2. `test_parse_rotowire_partial_morning` — early-morning fixture where many players have no status yet, verify no false "available" rows.
3. `test_parse_espn_html` — HTML fixture parses correctly.
4. `test_parse_espn_api_news_feed` — API fixture parses correctly as fallback.
5. `test_status_mapping_known_strings` — every entry in the mapping table maps correctly.
6. `test_status_mapping_unknown_falls_back` — unknown string maps to `unknown` and logs warning.
7. `test_status_mapping_case_insensitive` — "OUT", "out", "Out" all map to `out`.
8. `test_name_resolution_exact_alias` — alias hit returns correct player_id.
9. `test_name_resolution_diacritics` — "Luka Doncic" matches "Luka Dončić".
10. `test_name_resolution_team_scoped_fuzzy` — "L. James" + team=LAL matches LeBron.
11. `test_name_resolution_unresolved_writes_to_table` — unrecognizable name written to `unresolved_player_names`.
12. `test_dedup_same_status_same_day_no_insert` — same status logged twice in row produces one DB row.
13. `test_dedup_status_change_creates_new_row` — questionable → out creates two rows.
14. `test_loader_handles_traded_player` — player whose team changed mid-season resolves to current team.
15. `test_browser_fallback_triggers_on_cloudflare_marker` — mock cloudflare response triggers Playwright path.

### Integration Tests

16. `test_e2e_rotowire_live` — fetch real Rotowire page, parse, load. Verify rows appear in DB. Network-dependent; mark with `@pytest.mark.network`.
17. `test_e2e_cross_source_validation` — fetch both sources, run validator, verify reasonable agreement rate.
18. `test_status_timeline_for_player` — over a 24-hour period of fixtures, verify timeline is reconstructed correctly (no gaps, no duplicates, monotonic timestamps).

## Edge Cases to Handle Explicitly

- **Player traded mid-day** — name resolves to old team's player_id, but team_abbr in source is new team. Use most recent team in `players` table.
- **Two-way contract players** — appear/disappear from active rosters. Don't fail; resolve to player_id and store status.
- **Name collisions** — e.g., "Brandon Williams" (multiple players historically). Disambiguate via team_abbr; if team also matches multiple, log to unresolved.
- **Apostrophe variants** — D'Angelo Russell, De'Aaron Fox. Normalize by stripping apostrophes for matching but preserve in display.
- **Suffixes** — Larry Nance Jr., Tim Hardaway Jr. Strip "Jr.", "Sr.", "II", "III" for matching.
- **Late changes** — player listed as "questionable" at 6pm, then "out" 5 minutes before tip. Both rows must persist in `injury_status`. Feature engineering will use the most recent status before tipoff.
- **Inactive lists posted post-tipoff** — sometimes the official inactive list lands after the game starts. Capture but mark with a `late_confirm` flag or similar. Decision: don't add a column; the timestamp tells the truth.

## Out of Scope (Do Not Build)

- Real-time push/websocket updates (not available from these sources).
- Twitter/X scraping for breaking news (legal/ToS concerns; revisit later).
- Automatic feature recomputation on injury change — that's the orchestration spec's job.
- A web UI for resolving aliases — CLI tool is enough for now.

## Deliverables

In your PR description:

1. Confirmation each success criterion is met with verification commands and output snippets.
2. List of test cases passing (paste pytest output).
3. Output of `scripts/validate_injury_sources.py` on a real run.
4. Sample of `unresolved_player_names` after a real run (should be small or empty for current rosters).
5. Notes on which fetch method (httpx vs Playwright) you used per source and why.
6. Any deviations from this spec, with justification.

If anything is unclear, stop and ask Lincoln before writing code.
