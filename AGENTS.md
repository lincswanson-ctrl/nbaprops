# CLAUDE.md / AGENTS.md

This file is read by Claude, Codex, and Antigravity before any task. Read ARCHITECTURE.md first, then this.

## Project: NBA Prop Model

You are working on a sports betting prop model. Code quality matters because bugs cost real money.

## Always Do

- Read `ARCHITECTURE.md` and the relevant `/specs/*.md` file before writing code.
- Add tests for every new function. Use pytest.
- Use type hints everywhere. Use `mypy --strict` compatible code.
- Log with structured logging (`structlog`), never `print` in production code.
- Use `pydantic` models for any data crossing module boundaries.
- All timestamps in UTC, stored as `TIMESTAMP WITH TIME ZONE`.
- Use environment variables via `pydantic-settings` for all secrets and config.
- Write idempotent loaders — re-running must not duplicate rows.
- For DB writes use `INSERT ... ON CONFLICT` upserts keyed on natural keys from the schema.
- Keep functions under 50 lines. Refactor when they grow.
- Name features in `snake_case` and prefix by group: `form_pts_l10`, `opp_drtg_vs_pos`, etc.

## Never Do

- Never use `random_state=None` or non-deterministic splits in modeling code.
- Never train on data after the prediction target's game date.
- Never hardcode API keys, URLs, or magic numbers — use config.
- Never modify files outside the module you were assigned without flagging it.
- Never delete or rewrite migrations — add a new numbered migration instead.
- Never use `print` or `time.sleep(N)` in scrapers — use logger and proper rate limit handling.
- Never commit raw scraped HTML/JSON to git — it goes to object storage.
- Never bypass the spec. If the spec is wrong, stop and flag it.

## Code Style

- Black-formatted, line length 100.
- Imports: stdlib, third-party, first-party, separated by blank lines.
- Docstrings on every public function (Google style).
- Prefer composition over inheritance.
- Prefer pure functions in `/features` and `/betting`. Side effects only in `/data-pipeline` and `/orchestration`.

## Testing Conventions

- Unit tests in `/tests/unit/<module>/`
- Integration tests (hit DB) in `/tests/integration/`
- Fixtures in `/tests/fixtures/` — keep them small and committed
- Every scraper has a fixture file from a real response, used in tests
- Every feature has at least 3 test cases: typical, edge case, missing data
- Run: `pytest -x --cov=. --cov-report=term-missing`

## Working with the Database

- Migrations live in `/data-pipeline/schemas/NNN_description.sql`, numbered sequentially.
- Use a single connection pool from `data_pipeline/db.py`.
- Long-running operations use server-side cursors.
- Never run `DROP` or destructive operations in code — only in migrations reviewed by Lincoln.

## Definition of Done

A task is done when:
1. Code is written per spec.
2. Tests pass and cover the cases listed in the spec.
3. Docstrings and type hints are complete.
4. The change has been manually verified by running it once end-to-end with real data.
5. A short summary of what changed and what was verified is in the PR description.

## When Stuck

If a spec is ambiguous or incomplete, **stop and flag it in the PR description**. Do not guess at intent. Do not invent fields, tables, or features that aren't in the spec.

## Security

- No secrets in code or logs.
- Scraper user-agents must identify as a legit browser; rotate if needed.
- Respect robots.txt and rate limits — getting IPs banned breaks the pipeline.
- For nba.com/stats, the standard headers (Referer, User-Agent, x-nba-stats-*) are required and documented in `specs/01_nba_stats_scraper.md`.
