"""Today's Card page (minimal bootstrap)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import KPI, kpi_row, page_header
from dashboard.data import (
    count_games,
    filtered_card_edges,
    latest_edge_date,
    load_bettable_today_edges,
)


def render() -> None:
    selected_date, card_size = _render_top_controls()
    all_edges = load_bettable_today_edges(selected_date, include_debug=True)
    edges = filtered_card_edges(all_edges, card_size=card_size)

    page_header(
        "Today's Card",
        subtitle=f"{selected_date} · {count_games(edges)} games",
    )
    kpi_row(
        [
            KPI("Games", str(count_games(edges))),
            KPI("Picks", str(len(edges))),
        ]
    )
    if not edges:
        st.warning("No bettable edges found for this date.")
        return
    st.dataframe(_official_main_table_frame(edges), hide_index=True, width="stretch")


def _render_top_controls() -> tuple[str, int | None]:
    col_date, col_size, _ = st.columns([1, 1, 2])
    with col_date:
        selected = st.date_input(
            "Date",
            value=date.fromisoformat(latest_edge_date()),
        )
    with col_size:
        card_label = st.selectbox(
            "Show",
            options=["All official", "Top 5", "Top 8", "Top 12"],
            index=0,
        )
    return selected.isoformat(), _card_size_from_label(card_label)


def _card_size_from_label(label: str) -> int | None:
    if label == "All official":
        return None
    return int(label.removeprefix("Top "))


def _edges_frame(edges: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for edge in edges:
        rows.append(
            {
                "Player": edge.get("player_name", edge.get("player", "")),
                "Bet": _bet_label(edge),
                "Confidence": str(edge.get("filtered_confidence", edge.get("confidence", ""))).title()
                or "-",
                "Market": str(edge.get("market", "")),
                "Line": edge.get("line", ""),
                "Side": edge.get("side", ""),
                "Book": edge.get("book", edge.get("sportsbook", "")),
                "Edge": _pct_or_dash(edge.get("filtered_edge", edge.get("edge"))),
            }
        )
    return pd.DataFrame(rows)


def _official_main_table_frame(edges: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for edge in edges:
        rows.append(
            {
                "Bet": _bet_label(edge),
                "Grade": str(edge.get("final_grade", "—")),
                "Line": edge.get("line", ""),
                "Edge": _pct_or_dash(edge.get("filtered_edge", edge.get("edge"))),
                "Book": edge.get("book", edge.get("sportsbook", "")),
            }
        )
    return pd.DataFrame(rows)


def _bet_label(edge: dict[str, Any]) -> str:
    player = edge.get("player_name", edge.get("player", ""))
    market = edge.get("market", "")
    return f"{player} — {market}"


def _pct_or_dash(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _render_edge_detail(edge: dict[str, Any]) -> None:
    st.json(edge)
