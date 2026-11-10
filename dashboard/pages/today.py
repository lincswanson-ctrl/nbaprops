"""Today's Card page (minimal bootstrap)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import KPI, kpi_row, page_header
from dashboard.formatting import display_market
from dashboard.queries import (
    get_available_pick_dates,
    get_edge_distribution,
    get_parlay_suggestions,
    get_picks,
    get_summary,
)
from data_pipeline.seasons import current_nba_season


def render() -> None:
    selected_date, _card_size = _render_top_controls()
    summary = get_summary(selected_date)
    picks = get_picks(selected_date)
    picks = sorted(picks, key=lambda r: float(r.get("edge_pct", 0.0)), reverse=True)
    if _card_size is not None:
        picks = picks[:_card_size]
    edge_distribution = get_edge_distribution(selected_date)
    parlay_suggestions = get_parlay_suggestions(selected_date)

    page_header(
        "Today's Card",
        subtitle=f"{selected_date.isoformat()} · {summary['game_count']} games",
    )
    kpi_row(
        [
            KPI("Games", str(summary["game_count"])),
            KPI("Markets", str(summary["market_count"])),
            KPI("Picks", str(summary["pick_count"])),
            KPI("Avg Edge", _pct_or_dash(summary["avg_edge"])),
        ]
    )
    if not picks:
        st.info(
            "No picks generated yet - run: py scripts/run_pregame.py --date today --season "
            f"{current_nba_season()}"
        )
        return
    st.dataframe(_official_main_table_frame(picks), hide_index=True, width="stretch")
    if edge_distribution:
        st.caption("Edge distribution")
        st.bar_chart(pd.DataFrame({"edge_pct": edge_distribution}), y="edge_pct", width="stretch")
    if parlay_suggestions:
        st.subheader("Suggested Pair Plays")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Leg 1": row["leg_1"],
                        "Leg 2": row["leg_2"],
                        "Combined Edge": _pct_or_dash(row["combined_edge_pct"]),
                        "Reasoning": row["reasoning"],
                    }
                    for row in parlay_suggestions
                ]
            ),
            hide_index=True,
            width="stretch",
        )


def _render_top_controls() -> tuple[date, int | None]:
    col_date, col_size, _ = st.columns([1, 1, 2])
    available_dates = get_available_pick_dates()
    default_date = date.today()
    if available_dates and default_date not in available_dates:
        default_date = available_dates[0]
    with col_date:
        selected = st.date_input(
            "Date",
            value=default_date,
        )
    with col_size:
        card_label = st.selectbox(
            "Show",
            options=["All official", "Top 5", "Top 8", "Top 12"],
            index=0,
        )
    return selected, _card_size_from_label(card_label)


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
                "Game": f"{edge.get('away_team', '')} @ {edge.get('home_team', '')}",
                "Confidence": str(edge.get("confidence", "—")).title(),
                "Grade": str(edge.get("grade", "C-")),
                "Line": edge.get("line", ""),
                "Odds": edge.get("price", ""),
                "Edge": _pct_or_dash(edge.get("filtered_edge", edge.get("edge", edge.get("edge_pct")))),
                "Book": edge.get("book", edge.get("sportsbook", "")),
                "Reason": str(edge.get("grade_reason", "")),
            }
        )
    return pd.DataFrame(rows)


def _bet_label(edge: dict[str, Any]) -> str:
    player = edge.get("player_name", edge.get("player_id", "Unknown"))
    market = display_market(edge.get("market", ""))
    side = str(edge.get("side", "")).title()
    return f"{player} {market} {side}".strip()


def _pct_or_dash(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _render_edge_detail(edge: dict[str, Any]) -> None:
    st.json(edge)
