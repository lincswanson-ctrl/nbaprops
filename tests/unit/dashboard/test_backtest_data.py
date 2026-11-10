"""Unit tests for dashboard.backtest_data."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dashboard import backtest_data as bd

CAL = {
    "model_version": "baseline_v1",
    "markets": {
        "player_points": {
            "samples": 30,
            "base_rate_over": 0.5,
            "brier": 0.24,
            "brier_skill_vs_base_rate": 0.04,
            "log_loss": 0.67,
            "mean_abs_error": 4.1,
            "reliability": [
                {
                    "lower": 0.4,
                    "upper": 0.5,
                    "count": 10,
                    "mean_predicted": 0.45,
                    "observed_rate": 0.44,
                },
                {
                    "lower": 0.5,
                    "upper": 0.6,
                    "count": 20,
                    "mean_predicted": 0.55,
                    "observed_rate": 0.57,
                },
            ],
        },
        "player_threes": {"samples": 0},
    },
}
SUMMARY = {
    "model_version": "baseline_v1_blend",
    "by_side": {
        "over": {"bets": 3, "wins": 2, "losses": 1, "pushes": 0, "win_rate": 0.667, "roi": 0.2},
        "under": {"bets": 0},
    },
}


def test_list_labels_newest_first_and_ignores_other_files(tmp_path: Path) -> None:
    (tmp_path / "summary_old.json").write_text("{}")
    (tmp_path / "calibration_new.json").write_text("{}")
    (tmp_path / "bankroll_other.json").write_text("[]")
    os.utime(tmp_path / "summary_old.json", (1, 1))
    assert bd.list_labels(tmp_path) == ["new", "old"]
    assert bd.list_labels(tmp_path / "missing") == []


def test_list_labels_puts_placeholders_last(tmp_path: Path) -> None:
    (tmp_path / "summary_real.json").write_text(json.dumps({"model_version": "baseline_v1"}))
    (tmp_path / "summary_2024-25.json").write_text(json.dumps({"total_bets": 120}))
    os.utime(tmp_path / "summary_real.json", (1, 1))  # older, but generated
    assert bd.list_labels(tmp_path) == ["real", "2024-25"]


def test_load_json_and_is_generated(tmp_path: Path) -> None:
    good = tmp_path / "a.json"
    good.write_text(json.dumps(CAL))
    bad = tmp_path / "b.json"
    bad.write_text("{not json")
    assert bd.is_generated(bd.load_json(good))
    assert bd.load_json(bad) is None
    assert bd.load_json(tmp_path / "missing.json") is None
    assert not bd.is_generated({"total_bets": 120})  # legacy placeholder shape


def test_calibration_table_skips_empty_markets() -> None:
    table = bd.calibration_table(CAL)
    assert list(table["Market"]) == ["Points"]
    assert table.iloc[0]["Samples"] == 30
    assert bd.calibration_table({}).empty


def test_reliability_frame() -> None:
    frame = bd.reliability_frame(CAL, "player_points")
    assert list(frame.index) == [0.45, 0.55]
    assert list(frame["Observed"]) == [0.44, 0.57]
    assert bd.reliability_frame(CAL, "player_threes").empty


def test_breakdown_frame_skips_groups_without_bets() -> None:
    frame = bd.breakdown_frame(SUMMARY, "by_side")
    assert list(frame["Group"]) == ["over"]
    assert frame.iloc[0]["W-L-P"] == "2-1-0"
    assert bd.breakdown_frame(SUMMARY, "by_market").empty


def test_bankroll_frame() -> None:
    frame = bd.bankroll_frame([{"date": "2026-01-02", "bankroll": 101.0}])
    assert frame["bankroll"].tolist() == [101.0]
    assert bd.bankroll_frame(None).empty
    assert bd.bankroll_frame([{"x": 1}]).empty
