"""Performance / history page."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import KPI, kpi_row, page_header, section
from dashboard.formatting import pct
from dashboard.queries import get_dates_with_edges_or_picks, get_edge_distribution, get_picks, get_summary


def render() -> None:
    dates = get_dates_with_edges_or_picks()
    if not dates:
        page_header("Performance")
        st.info("No picks/edges found yet. Run pregame first.")
        return
    selected_date = st.sidebar.selectbox("Date", options=dates, index=0)
    picks = get_picks(selected_date)
    summary = get_summary(selected_date)
    edge_distribution = get_edge_distribution(selected_date)
    page_header("Performance", subtitle=f"Date {selected_date.isoformat()}")
    _render_kpi_strip(summary, picks)

    tab_overview, tab_market, tab_calib, tab_clv, tab_filters = st.tabs(["Overview", "By Market", "Calibration", "CLV", "Filter Performance"])
    with tab_overview:
        _render_overview_tab(edge_distribution)
    with tab_market:
        st.dataframe(_market_breakdown(picks), hide_index=True, width="stretch")
    with tab_calib:
        _render_calibration_tab(edge_distribution)
    with tab_clv:
        st.info("CLV requires graded close-line data; not yet available in DB tables.")
    with tab_filters:
        st.info("Filter performance uses graded outcomes and will populate after settlement ingestion.")


def _render_kpi_strip(summary: dict[str, Any], picks: list[dict[str, Any]]) -> None:
    kpi_row(
        [
            KPI("Games", str(summary.get("game_count", 0))),
            KPI("Markets", str(summary.get("market_count", 0))),
            KPI("Picks", str(summary.get("pick_count", 0))),
            KPI("Avg Edge", pct(summary.get("avg_edge", 0.0))),
            KPI("High Confidence", str(sum(1 for p in picks if str(p.get("confidence", "")).lower() == "high"))),
        ]
    )


def _render_overview_tab(edge_distribution: list[float]) -> None:
    if edge_distribution:
        frame = pd.DataFrame({"edge_pct": edge_distribution})
        st.bar_chart(frame, y="edge_pct", width="stretch")
    else:
        st.info("No edge distribution data for selected date.")


def _render_calibration_tab(edge_distribution: list[float]) -> None:
    with section("Edge distribution sanity"):
        if not edge_distribution:
            st.info("No edge rows to evaluate.")
            return
        frame = pd.DataFrame({"edge_pct": edge_distribution})
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "count": len(edge_distribution),
                        "min_edge": float(frame["edge_pct"].min()),
                        "max_edge": float(frame["edge_pct"].max()),
                        "mean_edge": float(frame["edge_pct"].mean()),
                    }
                ]
            ),
            hide_index=True,
            width="stretch",
        )


def _market_breakdown(picks: list[dict[str, Any]]) -> pd.DataFrame:
    if not picks:
        return pd.DataFrame([{"market": "-", "picks": 0, "avg_edge": "-"}])
    frame = pd.DataFrame(picks)
    grouped = frame.groupby("market", dropna=False)["edge_pct"].agg(["count", "mean"]).reset_index()
    grouped.columns = ["market", "picks", "avg_edge"]
    grouped["avg_edge"] = grouped["avg_edge"].apply(pct)
    return grouped
