"""Performance / history page."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import KPI, kpi_row, page_header, section
from dashboard.data import (
    get_available_results_dates,
    grade_hit_rate_summary_live,
    load_backtest_summary,
    load_bankroll_history,
    prob_bucket_calibration_live,
    results_metrics_by_scope,
)
from dashboard.formatting import money, pct
from engine.filter_performance import FilterPerformanceTracker


def render() -> None:
    season = st.sidebar.text_input("Season", value="2024-25")
    mode = st.sidebar.selectbox("Mode", ["Synthetic", "Live"])
    summary = load_backtest_summary(season)
    bankroll = load_bankroll_history(season)

    page_header("Performance", subtitle=f"Season {season} · {mode} mode")
    if not summary:
        st.warning("No backtest summary artifact found — showing live calibration only.")

    if summary:
        _render_kpi_strip(summary)

    tab_overview, tab_market, tab_calib, tab_clv, tab_filters = st.tabs(
        ["Overview", "By Market", "Calibration", "CLV", "Filter Performance"]
    )
    with tab_overview:
        _render_overview_tab(bankroll)
    with tab_market:
        if summary:
            st.dataframe(_market_breakdown(summary), hide_index=True, width="stretch")
        else:
            st.info("No market breakdown.")
    with tab_calib:
        _render_calibration_tab(summary or {})
    with tab_clv:
        st.info("CLV tables require full ledger — add graded artifacts under data/graded/.")
    with tab_filters:
        _render_filter_performance_tab()


def _render_kpi_strip(summary: dict[str, Any]) -> None:
    initial_bankroll = 1000.0
    final_bankroll = float(summary.get("final_bankroll", initial_bankroll))
    delta = final_bankroll - initial_bankroll
    kpi_row(
        [
            KPI("Total bets", str(summary.get("total_bets", 0))),
            KPI("Win rate", pct(summary.get("win_rate", 0.0))),
            KPI("ROI", pct(summary.get("roi", 0.0))),
            KPI(
                "Bankroll",
                f"{money(initial_bankroll)} → {money(final_bankroll)}",
                delta=f"{money(delta)}",
            ),
        ]
    )


def _render_overview_tab(bankroll: list[dict[str, Any]]) -> None:
    if bankroll:
        frame = pd.DataFrame(bankroll)
        st.line_chart(frame, x="date", y="bankroll", width="stretch")
    else:
        st.info("No bankroll history artifact found.")


def _render_calibration_tab(summary: dict[str, Any]) -> None:
    latest_dates = get_available_results_dates()
    latest = latest_dates[0] if latest_dates else None
    if latest:
        scoped = results_metrics_by_scope(latest)
        uni = scoped.get("universe", {}).get("projection", {})
        picks = scoped.get("picks", {}).get("projection", {})
        with section(
            "Universe vs picks projection health (latest graded day)",
            caption=(
                "Universe uses all available props; picks uses the full bettable filtered "
                "card (every pick that passed the filters)."
            ),
        ):
            kpi_row(
                [
                    KPI("Universe compared", str(uni.get("compared_rows", 0))),
                    KPI("Universe avg abs error", f"{float(uni.get('avg_abs_error', 0.0)):.2f}"),
                    KPI("Universe bias (A - P)", f"{float(uni.get('signed_bias', 0.0)):+.2f}"),
                    KPI("Picks compared", str(picks.get("compared_rows", 0))),
                    KPI("Picks avg abs error", f"{float(picks.get('avg_abs_error', 0.0)):.2f}"),
                    KPI("Picks bias (A - P)", f"{float(picks.get('signed_bias', 0.0)):+.2f}"),
                ]
            )
    if summary:
        with section("Confidence calibration", caption="Predicted vs realized hit rate by bucket."):
            st.dataframe(_calibration_table(summary), hide_index=True, width="stretch")
    with section(
        "Probability calibration (live ledger)",
        caption=(
            "Settled official picks bucketed by model probability. Larger expected vs "
            "actual gaps indicate miscalibration."
        ),
    ):
        prob_rows = prob_bucket_calibration_live()
        if not any(row["bets"] for row in prob_rows):
            st.info("No settled official picks with model_prob yet.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Bucket": row["bucket"],
                            "Bets": row["bets"],
                            "Expected": pct(row["expected_win_rate"]) if row["bets"] else "-",
                            "Actual": pct(row["actual_win_rate"]) if row["bets"] else "-",
                            "Delta": pct(row["delta"]) if row["bets"] else "-",
                        }
                        for row in prob_rows
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
    with section("Grade hit-rate (live ledger)"):
        grade_rows = grade_hit_rate_summary_live()
        if not grade_rows:
            st.info("No settled official picks with final_grade yet.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Grade": row["Grade"],
                            "Bets": row["Bets"],
                            "Hit%": pct(row["Hit%"]),
                            "ROI": pct(row["ROI"]),
                        }
                        for row in grade_rows
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def _render_filter_performance_tab() -> None:
    st.caption(
        "All graded player props grouped by market, side, role, and clearance bucket."
    )
    filter_report = FilterPerformanceTracker().generate_report()
    if filter_report.empty:
        st.info("No graded filter-performance data available yet.")
        return
    display = pd.DataFrame(
        {
            "Market": filter_report["market"],
            "Side": filter_report["side"],
            "Role": filter_report["player_role"],
            "Clearance": filter_report["clearance"],
            "Bets": filter_report["total_bets"],
            "Hit%": filter_report["hit_rate"].apply(pct),
            "ROI": filter_report["roi"].apply(pct),
            "CLV": filter_report["average_clv"].apply(pct),
        }
    )
    st.dataframe(display, hide_index=True, width="stretch")


def _market_breakdown(summary: dict[str, Any]) -> pd.DataFrame:
    by_market = summary.get("by_market")
    if isinstance(by_market, list):
        return pd.DataFrame(by_market)
    return pd.DataFrame(
        [
            {
                "market": "all",
                "bets": summary.get("total_bets", 0),
                "win_rate": pct(summary.get("win_rate", 0.0)),
                "roi": pct(summary.get("roi", 0.0)),
                "avg_edge": pct(summary.get("avg_edge", 0.0)),
            }
        ]
    )


def _calibration_table(summary: dict[str, Any]) -> pd.DataFrame:
    calibration = summary.get("calibration")
    if isinstance(calibration, list):
        return pd.DataFrame(calibration)
    hit_rates = summary.get("hit_rate_by_confidence", {})
    if not isinstance(hit_rates, dict):
        hit_rates = {}
    rows = []
    for confidence, win_rate in hit_rates.items():
        rows.append(
            {
                "bucket": str(confidence),
                "bets": "",
                "actual_win_rate": pct(win_rate),
                "expected": "",
            }
        )
    return pd.DataFrame(rows)
