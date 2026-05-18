"""Pipeline data freshness health page."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.components import KPI, kpi_row, page_header
from dashboard.queries import get_pipeline_health_rows


def _scan_edge_files(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((root / "edges").glob("*edges_*.json")):
        if not path.is_file():
            continue
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        rows.append(
            {
                "file": path.name,
                "date": path.stem.split("_")[-1],
                "bytes": path.stat().st_size,
                "modified_utc": stamp.isoformat(),
            }
        )
    return rows

def render() -> None:
    page_header("Pipeline Health")
    root = Path("data")
    rows = _scan_edge_files(root)
    if rows:
        newest = max(rows, key=lambda row: str(row["date"]))
        newest_date = str(newest["date"])
        staleness_days = (datetime.now(UTC).date() - date.fromisoformat(newest_date)).days
        kpi_row(
            [
                KPI("Newest Edge Date", newest_date),
                KPI("Staleness (days)", str(staleness_days)),
                KPI("Files Found", str(len(rows))),
            ]
        )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("No edge files found in data/edges/.")

    try:
        db_rows = get_pipeline_health_rows()
    except Exception:
        db_rows = []
    if db_rows:
        st.subheader("DB-based Health")
        st.dataframe(pd.DataFrame(db_rows), hide_index=True, width="stretch")
    else:
        st.caption("DB-based health unavailable; showing file-based health only.")
