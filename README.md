# NBA Props

**GitHub:** [github.com/lincswanson-ctrl/nbaprops](https://github.com/lincswanson-ctrl/nbaprops) · clone: `https://github.com/lincswanson-ctrl/nbaprops.git`

Streamlit dashboard for edge review plus agent specs for the full data pipeline. **Pregame edge filter** is implemented (`engine/edge_filter.py`, `scripts/filter_edges.py`, `orchestration/daily.py`). The full **data pipeline** (Postgres loaders, scrapers beyond odds, Prefect) is still mostly ahead of this checkout—see `specs/` and `ARCHITECTURE.md`.

## Run dashboard

```powershell
.\.venv\Scripts\activate
pip install -e .
streamlit run app.py
```

Or:

```powershell
python scripts/run_dashboard.py
```

Place edge artifacts under `data/edges/` (see sample files). The **NBA Props Launcher** expects the repo root to contain **`scripts/run_dashboard.py`**.

### Pregame filter (raw → filtered JSON)

```powershell
python scripts/filter_edges.py --date YYYY-MM-DD
```

Reads `data/edges/edges_<date>.json`, applies `config/filter_rules.yaml`, writes `data/edges/filtered_edges_<date>.json`. Same logic is available in-process as `orchestration.daily.run_pregame_edge_filter`.

After `pip install -e ".[dev]"`, run `python -m pytest tests/unit/engine tests/unit/orchestration -q` to verify the filter and orchestration helpers (other unit tests may be skipped if optional packages are missing).

## Daily commands

### Game day (generates picks for today)

```powershell
py scripts/run_pregame.py --date today --season 2026-27
```

### Specific date

```powershell
py scripts/run_pregame.py --date 2026-11-10 --season 2026-27
```

### Generate picks + open dashboard

```powershell
py scripts/run_pregame.py --date today --season 2026-27 --open-dashboard
```

### CSV odds fallback (if live books fail)

Create `data/odds/manual_odds_YYYY-MM-DD.csv` with columns:
`game_id,market,book,line,price,odds_timestamp`

Then run:

```powershell
py scripts/run_pregame.py --date 2026-11-10 --season 2026-27 --fail-fast
```

Optional explicit path override:

```powershell
$env:PREGAME_ODDS_CSV="C:\path\to\manual_odds.csv"
py scripts/run_pregame.py --date 2026-11-10 --season 2026-27
```

### Dashboard only (assumes picks already generated)

```powershell
py scripts/run_dashboard.py
```

## Docs and specs

| Path | Purpose |
|------|---------|
| `ARCHITECTURE.md` | System overview and intended repo layout |
| `AGENTS.md` / `CLAUDE.md` | Agent rules (read before coding) |
| `ANTIGRAVITY_KICKOFF.md` | Paste-into-Antigravity build prompt |
| `specs/01_nba_stats_scraper.md` … `04_referee_scraper.md` | Scraper / loader specs (canonical copy lives here only) |
