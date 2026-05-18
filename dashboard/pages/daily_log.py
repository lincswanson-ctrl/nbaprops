"""Daily graded-bet log page."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from dashboard.components import KPI, kpi_row, page_header
from dashboard.queries import get_available_pick_dates, get_picks_by_date


def render() -> None:
    dates = get_available_pick_dates()
    if not dates:
        st.title("Daily Log")
        st.info("No pregame picks found in database yet.")
        return
    selected = st.date_input("Date", value=dates[0])
    rows = get_picks_by_date(selected)
    page_header("Daily Log", subtitle=selected.isoformat())
    if not rows:
        st.info("No picks found for this date.")
        return

    wins = 0
    losses = 0
    pnl = 0.0
    kpi_row(
        [
            KPI("Total Picks", str(len(rows))),
            KPI("Wins", str(wins)),
            KPI("Losses", str(losses)),
            KPI("Net P&L", f"{pnl:+.2f}u (pending)"),
        ]
    )
    frame = pd.DataFrame(
        [
            {
                "Game": f"{row.get('away_team', '')} @ {row.get('home_team', '')}",
                "Market": row.get("market", ""),
                "Line": row.get("line", ""),
                "Side": row.get("side", ""),
                "Confidence": row.get("confidence", ""),
                "Edge": f"{float(row.get('edge_pct', 0.0)) * 100:.1f}%",
                "Status": row.get("status", ""),
            }
            for row in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")
