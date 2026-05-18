"""Historical card browser page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.components import KPI, kpi_row, page_header
from dashboard.queries import get_dates_with_edges_or_picks, get_edges, get_picks_by_date
from dashboard.pages.today import _official_main_table_frame


def render() -> None:
    dates = get_dates_with_edges_or_picks()
    st.sidebar.subheader("Card Dates")
    if not dates:
        st.title("Card History")
        st.info("No filtered edge artifacts found yet.")
        return
    selected_date = st.sidebar.selectbox("Date", options=dates, index=0)
    edges = get_picks_by_date(selected_date)
    all_edges = get_edges(selected_date)
    page_header("Card History", subtitle=selected_date.isoformat())

    picks_count = len(edges)
    settled = 0
    hit_rate = None
    kpi_row(
        [
            KPI("Picks", str(picks_count)),
            KPI("Edges", str(len(all_edges))),
            KPI("Graded", "N/A"),
            KPI("Hit Rate", f"{hit_rate:.1%}" if hit_rate is not None else "—"),
        ]
    )
    if not edges:
        st.info("No official card rows for this date.")
        return
    frame = _official_main_table_frame(edges)
    st.dataframe(pd.DataFrame(frame), hide_index=True, width="stretch")
