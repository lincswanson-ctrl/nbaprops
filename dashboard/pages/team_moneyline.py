"""Team moneyline edges page."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from dashboard.components import page_header
from dashboard.queries import get_available_pick_dates, get_team_moneyline_rows


def render() -> None:
    dates = get_available_pick_dates()
    default_date = dates[0] if dates else date.today()
    selected = (
        st.date_input("Date", value=default_date)
        if dates
        else st.date_input("Date", value=date.today())
    )
    rows = get_team_moneyline_rows(selected)
    page_header("Team Moneyline", subtitle=selected.isoformat())
    if not rows:
        st.info(
            "No team moneyline picks found for this date."
        )
        return
    frame = pd.DataFrame(
        [
            {
                "Matchup": f"{row.get('away_team', '')} @ {row.get('home_team', '')}",
                "Market": row.get("market", ""),
                "Line": row.get("line", ""),
                "Edge": f"{float(row.get('edge_pct', 0.0)) * 100:.1f}%",
                "Side": row.get("side", ""),
                "Confidence": row.get("confidence", ""),
            }
            for row in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")
