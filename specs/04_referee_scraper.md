# Spec: NBA Referee Assignments Scraper + Loader

**Module:** `data-pipeline/scrapers/officials.py` and `data-pipeline/loaders/officials.py`
**Owner:** Antigravity (autonomous build, Claude reviews PR)
**Depends on:** `specs/01_nba_stats_scraper.md` (needs `games` table)
**Estimated effort:** 1 day end-to-end
**Cost:** $0 — public NBA data

---

## Goal

Capture the three-person referee crew assigned to every NBA game, both prospectively (announced morning of game day) and retrospectively (final crew from box score). Referee tendencies meaningfully affect prop outcomes — some refs call 50+ fouls per game, others let games breathe; this directly impacts free throw volume, foul trouble for stars, and total points scored. The model uses ref crew features to adjust point projections.

## Why This Matters (Briefly)

- **Foul rates vary 30%+ across NBA refs.** A high-foul crew adds 4-6 free throws to a typical star's prop projection.
- **Pace impact is real.** Some crews whistle constantly (slow pace, more stops, fewer possessions) — others let plays continue (faster pace, more possessions).
- **Foul trouble distribution.** Stars with 2 early fouls sit longer under strict crews — directly affecting minutes and points.
- **Books are slow to adjust.** Ref assignments come out the morning of games; book lines often don't move on this info. This is one of the cleaner edges in NBA props.

## Data Sources

### Primary: official.nba.com (Prospective + Retrospective)

URL: `https://official.nba.com/referee-assignments/`

This is the official NBA page that announces ref assignments each morning of game day. Format: HTML table with date, game (home @ away), crew chief, referee, umpire.

Update timing: typically posted 9-11am ET morning of game day.

Method: HTML scrape. Page is server-rendered, no API. Use `httpx` with browser-like headers, fall back to Playwright if blocked.

### Secondary: stats.nba.com Box Score (Retrospective)

The `boxscoresummaryv2` endpoint (already used by spec 01) includes the final officials crew under the `Officials` result set. This is post-game truth — captures who actually worked the game even if there was a late switch.

Use this for backfill (every historical game already loaded by spec 01) and as validation for the prospective scrape.

## What This Spec Adds Beyond Spec 01

Spec 01 already calls `boxscoresummaryv2` for game metadata. This spec extends that to extract the officials data and adds the prospective scraper for same-day announcements (which spec 01 doesn't cover because spec 01 only runs after games complete).

## Success Criteria

1. Running `python -m data_pipeline.scrapers.officials --once` fetches today's ref assignments from official.nba.com and saves raw HTML to storage.
2. Running `python -m data_pipeline.loaders.officials --source nba_official` populates `game_officials` and `officials` tables.
3. Running `python -m data_pipeline.loaders.officials --source nba_stats --season 2024-25` backfills officials data from already-loaded `boxscoresummaryv2` raw responses.
4. Cross-validation script confirms prospective announcements match post-game box score officials in ≥98% of games (the rare miss is usually a late substitution).
5. Per-referee aggregate stats can be computed via SQL (sample query in PR description): games worked, avg fouls per game, avg FTAs per game, pace under this ref.
6. Pytest suite passes.

## Database Schema (Migration `004_officials.sql`)

```sql
-- Master list of NBA referees
CREATE TABLE officials (
    official_id TEXT PRIMARY KEY,         -- nba.com person_id when available, else generated
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    full_name TEXT NOT NULL,              -- canonical "Scott Foster", "James Capers", etc.
    jersey_number TEXT,                   -- referees have jersey numbers; useful as secondary key
    nba_stats_person_id INT,              -- link to nba.com if/when we discover it
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_officials_full_name ON officials(LOWER(full_name));

-- Per-game officials (one row per game with three refs)
-- Replaces the placeholder game_officials table from ARCHITECTURE.md
DROP TABLE IF EXISTS game_officials;

CREATE TABLE game_officials (
    game_id TEXT PRIMARY KEY REFERENCES games(game_id),
    crew_chief_id TEXT REFERENCES officials(official_id),
    referee_id TEXT REFERENCES officials(official_id),
    umpire_id TEXT REFERENCES officials(official_id),
    source TEXT NOT NULL,                 -- 'nba_official_prospective' | 'nba_stats_box_score'
    announced_at TIMESTAMPTZ,             -- when the prospective announcement was scraped
    confirmed_at TIMESTAMPTZ,             -- when the post-game truth was loaded
    is_late_change BOOLEAN DEFAULT FALSE, -- true if box score crew differs from announced
    raw_response_path TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_game_officials_crew_chief ON game_officials(crew_chief_id);
CREATE INDEX idx_game_officials_referee ON game_officials(referee_id);
CREATE INDEX idx_game_officials_umpire ON game_officials(umpire_id);

-- Track late changes for analysis (rare but valuable info — shows when assignments shift)
CREATE TABLE official_assignment_changes (
    id BIGSERIAL PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(game_id),
    role TEXT NOT NULL,                   -- 'crew_chief' | 'referee' | 'umpire'
    announced_official_id TEXT REFERENCES officials(official_id),
    actual_official_id TEXT REFERENCES officials(official_id),
    detected_at TIMESTAMPTZ DEFAULT NOW()
);
```

## File Structure

```
data-pipeline/
├── scrapers/
│   └── officials.py                  # nba.com/officials prospective scrape
├── loaders/
│   ├── officials.py                  # both sources -> DB
│   └── official_resolution.py        # name -> official_id with fuzzy match
├── schemas/
│   └── 004_officials.sql
features/
└── (referee feature group comes later in spec 11; not built here)
tests/
├── fixtures/
│   └── officials/
│       ├── nba_official_assignments_sample.html
│       ├── nba_official_assignments_partial.html  # mid-morning, some games not yet announced
│       └── boxscore_summary_officials_sample.json
├── unit/
│   ├── scrapers/test_officials.py
│   └── loaders/test_officials.py
└── integration/
    └── test_officials_e2e.py
scripts/
└── validate_officials_cross_source.py
```

## Implementation Requirements

### Scraper (`scrapers/officials.py`)

- Class `NBAOfficialsScraper` with method `fetch_assignments(date: date) -> bytes`.
- Saves raw HTML to `<root>/raw/officials/nba_official/<UTC_date>/<scraped_at>.html` before parsing.
- Browser fallback to Playwright if httpx returns Cloudflare or empty response.
- CLI: `python -m data_pipeline.scrapers.officials --once` (today) or `--date YYYY-MM-DD` (specific date).
- For `--watch` mode, poll every 60 minutes between 9am ET and 1pm ET on game days (assignments typically post by 11am).

### Parser

NBA's official referee assignments page renders as an HTML table. Parse with `selectolax`:

```
| Date       | Game                              | Crew Chief    | Referee        | Umpire        |
| 11/15/2025 | Boston Celtics @ Lakers           | Scott Foster  | James Capers   | Mark Lindsay  |
| 11/15/2025 | Heat @ Knicks                     | ...           | ...            | ...           |
```

Edge cases:
- Sometimes only crew chief and referee are listed (umpire TBD); store NULL for missing role.
- Team names use full names like "Los Angeles Lakers" — must resolve to our team_id.
- A ref's name may appear differently across days ("Scott Foster" vs "S. Foster" vs "Scott Foster Jr." — handle via name resolution).

### Loader (`loaders/officials.py`)

Two ingestion paths:

**Path A: Prospective (from official.nba.com scrape):**
1. For each row in the parsed HTML, resolve teams → game_id (use date + home/away to look up `games`).
2. For each official name, resolve via `official_resolution.py` (creating new `officials` row if unknown).
3. Upsert `game_officials` row with `source='nba_official_prospective'` and `announced_at=now()`.
4. If a row already exists for this game_id from this source, only update if officials changed.

**Path B: Retrospective (from `boxscoresummaryv2` raw):**
1. Iterate raw `boxscoresummaryv2` JSONs from spec 01's storage.
2. Extract officials from the `Officials` result set (typically structured as: `[{'OFFICIAL_ID', 'FIRST_NAME', 'LAST_NAME', 'JERSEY_NUM'}, ...]` — confirm by inspecting a real fixture during build).
3. Resolve to `officials.official_id` (creating new rows if unknown — this gives us NBA's internal `nba_stats_person_id`).
4. Upsert `game_officials` row with `source='nba_stats_box_score'` and `confirmed_at=now()`.
5. If a prospective row exists and differs from box score, flip `is_late_change=true` and write to `official_assignment_changes`.

### Name Resolution (`loaders/official_resolution.py`)

NBA officials are a small fixed set (~70-75 active refs at any time), so resolution is much simpler than player resolution. Algorithm:

1. **Exact match on `LOWER(full_name)`** — primary path, hits 99%+ of cases.
2. **Initialed match** — "S. Foster" matches "Scott Foster" if exactly one ref has matching last name + first initial.
3. **Fuzzy match (rapidfuzz, ratio ≥ 92)** — last resort.
4. **Create new official** — if unmatched after fuzzy, create a new `officials` row using the parsed name. Log a warning so Lincoln can verify it's not a typo.

Suffixes (Jr., Sr., II) are stripped for matching but preserved in `full_name`.

When the box score loader provides `nba_stats_person_id`, backfill it to existing matched officials so future joins are by ID, not name.

### Validator (`scripts/validate_officials_cross_source.py`)

Runs after each game day completes:

```
Validating officials cross-source for 2025-11-15

Games on 2025-11-15: 12
Games with prospective announcement: 12
Games with box-score truth: 12

Crew matches (all 3 refs identical): 11 (91.7%)
Crew with 1+ swap: 1
  - Heat @ Knicks: announced umpire 'Mark Lindsay', actual 'Tony Brothers'
    -> recorded in official_assignment_changes

Total ref-game-slots compared: 36
Exact matches: 35 (97.2%)
```

Below 90% match rate consistently means parser drift — alert.

## Test Cases (Required)

### Unit Tests

1. `test_parse_assignments_full_table` — happy path fixture parses all 12 games.
2. `test_parse_assignments_missing_umpire` — row with only 2 listed refs parses correctly with umpire=None.
3. `test_parse_assignments_partial_morning` — fixture with some "TBD" rows parses without false rows.
4. `test_resolve_official_exact_match` — "Scott Foster" → existing official_id.
5. `test_resolve_official_initialed_match` — "S. Foster" with last name unique → existing official.
6. `test_resolve_official_initialed_collision` — "J. Williams" with multiple matching refs → fuzzy or unresolved.
7. `test_resolve_official_creates_new` — unknown ref creates new row, logs warning.
8. `test_resolve_official_strips_suffix` — "Tony Brothers Jr." matches "Tony Brothers" if no other Brothers.
9. `test_loader_prospective_idempotent` — load same announcement twice, no duplicate rows.
10. `test_loader_box_score_overrides_prospective` — prospective + matching box score = `confirmed_at` populated, no late change.
11. `test_loader_detects_late_change` — prospective umpire A, box score umpire B → `is_late_change=true`, row in `official_assignment_changes`.
12. `test_loader_box_score_alone` — historical game with only box score data loads cleanly without prospective row.
13. `test_team_name_resolution_for_game_lookup` — "Boston Celtics @ Los Angeles Lakers" on date → correct game_id.

### Integration Tests

14. `test_e2e_today_assignments` — fetch live page, parse, load. Verify rows in DB. Network-dependent, marked.
15. `test_e2e_full_season_backfill` — load officials from a full season's box score raws, verify ≥99% of games have a `game_officials` row.
16. `test_per_ref_aggregate_query` — after backfill, run aggregate SQL: games worked per ref, FTA per game allowed. Verify reasonable values.

## Aggregate Sanity Check (Include in PR)

Run this SQL after backfill and paste output in PR description:

```sql
-- Top 10 refs by games worked in 2024-25, with avg FTAs per game
SELECT
    o.full_name,
    COUNT(DISTINCT go.game_id) AS games_worked,
    ROUND(AVG(g.home_score + g.away_score)::numeric, 1) AS avg_total_pts,
    ROUND(AVG(
        (SELECT SUM(fta) FROM player_game_stats pgs WHERE pgs.game_id = g.game_id)
    )::numeric, 1) AS avg_total_ftas
FROM officials o
JOIN game_officials go ON (o.official_id IN (go.crew_chief_id, go.referee_id, go.umpire_id))
JOIN games g ON g.game_id = go.game_id
WHERE g.season = '2024-25'
GROUP BY o.full_name
ORDER BY games_worked DESC
LIMIT 10;
```

Expected: top refs work ~70-75 games, avg FTAs per game varies meaningfully across refs (the spread is the signal we'll use as a feature).

## Out of Scope (Do Not Build)

- Per-referee feature engineering — that's spec 11 (referee feature group).
- Referee tendency stats beyond raw counts (e.g., home/road bias, specific player histories) — defer to spec 11.
- G-League or summer league refs.
- Playoff officiating crews handled separately — playoff games come through the same endpoints; no special code needed.

## Deliverables

In the PR description:

1. Confirmation each success criterion is met with verification command + output snippets.
2. Pytest output showing all tests passing.
3. Output of `validate_officials_cross_source.py` on a real game day.
4. Output of the aggregate sanity check SQL on a backfilled season.
5. Notes on any official.nba.com page format quirks discovered during build.
6. Any deviations from spec, with justification.

If anything is unclear, stop and ask Lincoln before writing code.
