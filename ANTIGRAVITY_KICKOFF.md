# Antigravity Kickoff Prompt

Copy everything below the line into Antigravity to start the build.

---

You are building the data foundation of an **NBA-only** sports betting prop model. Specs in this repo: NBA stats (`specs/01_nba_stats_scraper.md`), injury data (`specs/02_injury_scraper.md`), odds data (`specs/03_odds_scraper.md`), and referee assignments (`specs/04_referee_scraper.md`). Build specs **01 → 02 → 03** in strict order; each depends on the prior. Spec **04** depends on spec 01 (`games` table). Read `ARCHITECTURE.md` and `CLAUDE.md` first.

**Scope is locked to NBA.** Do not add abstractions, base classes, or "sport-agnostic" interfaces with the future intent of supporting other leagues. This repo is NBA-only forever. Tables, columns, modules, and functions can name NBA concepts directly (teams, players, games — not "competitor", "athlete", "match").

## Build Order (Strict)

1. **Spec 01: NBA stats scraper + loader.** Foundation for everything. The `players` and `games` tables it builds are required by specs 02 and onward. Do not start spec 02 until spec 01 is done, tested, and the validation script passes.
2. **Spec 02: Injury scraper + loader.** Requires `players` table from spec 01 for name resolution.
3. **Spec 03: Odds scraper + loader.** Requires `players`, `games`, and the `name_resolution.py` module from spec 02. **This spec submits as TWO PRs:** first an endpoint discovery PR (use the browser tool to find sportsbook JSON endpoints), then the implementation PR. Lincoln approves the discovery PR before you write the scraper code.

The full system is **$0/month** — direct sportsbook scraping, no paid APIs. Everything in the build is free.

You may submit one PR per spec, or three small PRs within each spec (scraper, loader, validation), as long as each PR is independently reviewable and the build order above is respected.

## Working Approach (Per Spec)

1. **Read first.** `ARCHITECTURE.md` + `CLAUDE.md` + the active spec. Confirm understanding before any code.
2. **Repo skeleton first** if this is the first spec. Postgres connection helper, dependency management, test scaffolding, CI config.
3. **Migration first.** Write the SQL migration, apply it to a fresh Postgres, verify schema.
4. **Scraper next.** One endpoint/source end-to-end including raw storage, retry, tests, fixtures from real responses. Verify against the live source for one example.
5. **Loader next.** Pydantic models + loader for that endpoint. Verify idempotency with a double-run row-count test.
6. **Expand to all endpoints/sources** in the spec following the same pattern.
7. **Validation script** as defined in the spec. Run it on real data, paste output in PR.
8. **Full end-to-end smoke test** (e.g., one full day of data for spec 01, one polling cycle for spec 03).

## Environment

- Postgres: assume connection string in `DATABASE_URL` env var
- Raw storage: local at `./data/raw/` for dev (S3 path comes later)
- Python 3.11, use `pyproject.toml` with `uv` for dependency management
- No paid API keys required. Everything in the build is free public data.
- All dependencies must be justified — see CLAUDE.md "Never Do" list

## When You Hit a Decision Point

- If the spec is unambiguous, follow it.
- If the spec is ambiguous, **stop and write the question in your output** before proceeding. Do not guess.
- If you discover a bug in the spec (e.g., a schema mismatch with real API responses, a dependency gap between specs), document the issue and propose a fix; do not silently work around it.

## Anti-Goals (Things You Should Not Do)

- Do not jump ahead to specs not yet started. Specs after 03 (features, modeling, betting, dashboard) will be released later.
- Do not add features not in the active spec.
- Do not pre-optimize. Get correctness first, performance later.
- Do not skip tests because the code "looks right."
- Do not commit raw scraped JSON/HTML to git — use the configured storage path.
- Do not invent Postgres tables, columns, or indexes not in the migration spec for that spec.
- Do not generalize across sports.

## Definition of Done (Per Spec)

You hand the PR(s) for a spec to Lincoln when:

1. All success criteria in the spec are demonstrated.
2. All listed test cases pass (paste output in PR description).
3. Validation scripts pass on real data (paste output).
4. The PR description summarizes: what was built, what was verified, any deviations from spec with justification, any open questions.
5. The next spec's prerequisites are confirmed met (e.g., after spec 01, confirm `players` table is populated and queryable).

## Start Here

Begin by reading `ARCHITECTURE.md`, `CLAUDE.md`, and `specs/01_nba_stats_scraper.md`. Confirm your build plan for spec 01 in a short message before starting code. Then build spec 01. When it is fully merged and verified, move to spec 02. When that is fully merged and verified, move to spec 03.

---
