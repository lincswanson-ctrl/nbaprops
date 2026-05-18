"""Research Lab page."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import KPI, kpi_row, page_header
from dashboard.queries import (
    get_available_pick_dates,
    get_edges,
    get_picks,
    get_rejected_edges,
)
from dashboard.formatting import display_market, pct
from dashboard.pages.today import _edges_frame, _render_edge_detail


def render() -> None:
    """Render research / debug edge views."""
    selected_date = _render_top_controls()
    raw_edges = get_edges(selected_date)
    official_picks = get_picks(selected_date)
    all_filtered = raw_edges
    rejected = get_rejected_edges(selected_date)

    page_header("Research Lab", subtitle=f"Date: {selected_date.isoformat()}")
    kpi_row(
        [
            KPI("Raw edges", str(len(raw_edges))),
            KPI("Bettable (passed filters)", str(len(official_picks))),
            KPI("All filtered", str(len(all_filtered))),
            KPI("Rejected", str(len(rejected))),
        ]
    )

    tab_raw, tab_bettable, tab_all, tab_reject = st.tabs(
        ["Raw", "Bettable", "All filtered", "Rejected"]
    )
    with tab_raw:
        if not raw_edges:
            st.info("No raw edges artifact for this date.")
        else:
            st.dataframe(_raw_edges_frame(raw_edges), hide_index=True, width="stretch")
    with tab_bettable:
        if not official_picks:
            st.info("No bettable picks for this date.")
        else:
            st.dataframe(_edges_frame(official_picks), hide_index=True, width="stretch")
    with tab_all:
        st.caption(
            "Includes every bettable pick plus rows rejected by risk, quality, "
            "or research-only rules."
        )
        if not all_filtered:
            st.warning("No filtered_edges artifact for this date.")
        else:
            st.dataframe(_edges_frame(all_filtered), hide_index=True, width="stretch")
    with tab_reject:
        if not rejected:
            st.info("No rejected / research-only rows for this date.")
        else:
            st.dataframe(_rejected_edges_frame(rejected), hide_index=True, width="stretch")
            for edge in rejected:
                with st.expander(f"{_player_label(edge)} details"):
                    _render_edge_detail(edge)


def _render_top_controls() -> date:
    dates = get_available_pick_dates()
    default_date = date.today()
    if dates and default_date not in dates:
        default_date = dates[0]
    selected = st.date_input(
        "Date",
        value=default_date,
        help="Pick a date with generated picks/edges.",
    )
    return selected


def _rejected_edges_frame(edges: list[dict[str, Any]]) -> pd.DataFrame:
    frame = _edges_frame(edges)
    reason_values = [rejected_reason_display(edge) for edge in edges]
    risk_tags_values = [
        ", ".join(str(tag) for tag in edge.get("risk_tags", []))
        if isinstance(edge.get("risk_tags"), list) and edge.get("risk_tags")
        else "-"
        for edge in edges
    ]
    frame.insert(2, "Reason", reason_values)
    frame.insert(3, "Risk Tags", risk_tags_values)
    return frame


def rejected_reason_display(edge: dict[str, Any]) -> str:
    """Human-readable rejection reason with fallbacks."""
    for key in ("reason", "reasoning", "quality_reason"):
        value = edge.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    tags = edge.get("risk_tags")
    if isinstance(tags, list) and tags:
        joined = "; ".join(str(tag).strip() for tag in tags if str(tag).strip())
        if joined:
            return joined
    return "-"


def _raw_edges_frame(edges: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for edge in edges:
        rows.append(
            {
                "Player": edge.get("player_name", edge.get("player", "")),
                "Market": display_market(edge.get("market", "")),
                "Line": edge.get("line", ""),
                "Side": edge.get("side", ""),
                "Book": edge.get("book", edge.get("sportsbook", "")),
                "Edge": pct(edge.get("edge")) if _num(edge.get("edge")) is not None else "-",
                "Confidence": str(edge.get("confidence", "")).title() or "-",
            }
        )
    return pd.DataFrame(rows)


def _player_label(edge: dict[str, Any]) -> str:
    value = edge.get("player_name", edge.get("player", edge.get("player_id")))
    return str(value) if value is not None and str(value).strip() else "Unknown player"


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
