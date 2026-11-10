"""Backtest page: walk-forward calibration and betting results (specs 07/08)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from dashboard import backtest_data as bd
from dashboard.components import KPI, kpi_row, page_header, section


def _pct(value: Any) -> str:
    return "–" if value is None else f"{float(value) * 100:.1f}%"


def render(root: Path = bd.BACKTEST_DIR) -> None:
    """Render the backtest page for the run label chosen in the sidebar."""
    labels = bd.list_labels(root)
    if not labels:
        page_header("Backtest")
        st.info(
            "No backtest results yet. Run `py -m orchestration.backtest --season 2025-26` "
            "(see README) to create files in data/backtest/."
        )
        return
    label = st.sidebar.selectbox("Backtest run", options=labels, index=0)
    calibration = bd.load_json(root / f"calibration_{label}.json")
    summary = bd.load_json(root / f"summary_{label}.json")
    page_header("Backtest", subtitle=f"Run: {label}")
    tab_cal, tab_bet = st.tabs(["Calibration", "Betting"])
    with tab_cal:
        _render_calibration(calibration)
    with tab_bet:
        _render_betting(summary, bd.load_json(root / f"bankroll_{label}.json"), root, label)


def _render_calibration(report: Any) -> None:
    if not bd.is_generated(report):
        st.info("No calibration file for this run (`--mode calibration`).")
        return
    st.caption(
        f"{report.get('model_version')} · {report.get('start')} → {report.get('end')} · "
        "each player-game scored with only earlier games, at proxy lines near where books set them."
    )
    table = bd.calibration_table(report)
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(
        "Skill vs base rate > 0 means the model beats always guessing the average; "
        "Brier and log loss: lower is better."
    )
    markets = [m for m, s in (report.get("markets") or {}).items() if s.get("samples")]
    if not markets:
        return
    market = st.selectbox(
        "Reliability for market", markets, format_func=lambda m: bd.MARKET_LABELS.get(m, m)
    )
    frame = bd.reliability_frame(report, market)
    with section("Predicted vs observed", "On the diagonal = well calibrated."):
        st.line_chart(frame[["Observed", "Perfect"]])
        st.dataframe(frame.reset_index(), hide_index=True, width="stretch")


def _render_betting(summary: Any, curve: Any, root: Path, label: str) -> None:
    if summary is None:
        st.info("No betting file for this run (`--mode betting`).")
        return
    if not bd.is_generated(summary):
        st.warning(
            "Placeholder file, not produced by orchestration.backtest; ignore these numbers."
        )
        return
    weights = summary.get("blend_weights")
    st.caption(
        f"{summary.get('model_version')} · {summary.get('start')} → {summary.get('end')} · "
        f"blend weights: {weights if weights is not None else 'none (raw model)'} · "
        "flat 1u stakes at closing prices"
    )
    kpi_row(
        [
            KPI("Bets", str(summary.get("bets", 0))),
            KPI("Win rate", _pct(summary.get("win_rate")), help="Break-even at -110 is 52.4%"),
            KPI("ROI", _pct(summary.get("roi"))),
            KPI("Profit", f"{summary.get('profit_units', 0):+.2f}u"),
            KPI("Avg claimed edge", _pct(summary.get("avg_edge"))),
        ]
    )
    bankroll = bd.bankroll_frame(curve)
    if not bankroll.empty:
        with section("Cumulative profit (units)", "Flat 1-unit stakes; 0 = break-even."):
            profit = bankroll.rename(columns={"bankroll": "profit"}) - bd.BANKROLL_START_UNITS
            st.line_chart(profit)
    for key, title in (
        ("by_market", "By market"),
        ("by_side", "By side"),
        ("by_edge_bucket", "By edge"),
    ):
        with section(title):
            st.dataframe(bd.breakdown_frame(summary, key), hide_index=True, width="stretch")
    bets_path = root / f"bets_{label}.csv"
    if bets_path.exists():
        st.download_button("Download bets CSV", bets_path.read_bytes(), file_name=bets_path.name)
