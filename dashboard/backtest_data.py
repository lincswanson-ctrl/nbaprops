"""Read and shape walk-forward backtest outputs (spec 07/08) for the dashboard."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

BACKTEST_DIR = Path("data/backtest")
# Starting bankroll used by orchestration.backtest.bankroll_curve.
BANKROLL_START_UNITS = 100.0
_LABEL_RE = re.compile(r"^(?:summary|calibration)_(?P<label>.+)\.json$")
MARKET_LABELS = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "Threes",
    "player_points_rebounds_assists": "PRA",
}


def list_labels(root: Path = BACKTEST_DIR) -> list[str]:
    """Backtest run labels with a summary or calibration file.

    Runs written by ``orchestration.backtest`` come first (newest first); legacy placeholder
    files sort last so they are never the default selection.
    """
    if not root.exists():
        return []
    newest: dict[str, float] = {}
    generated: dict[str, bool] = {}
    for path in root.glob("*.json"):
        match = _LABEL_RE.match(path.name)
        if match:
            label = match.group("label")
            newest[label] = max(newest.get(label, 0.0), path.stat().st_mtime)
            generated[label] = generated.get(label, False) or is_generated(load_json(path))
    return sorted(newest, key=lambda label: (not generated[label], -newest[label], label))


def load_json(path: Path) -> Any:
    """Parse ``path`` as JSON, or return ``None`` if it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_generated(payload: Any) -> bool:
    """True for files written by ``orchestration.backtest`` (they record ``model_version``)."""
    return isinstance(payload, dict) and "model_version" in payload


def calibration_table(report: dict[str, Any]) -> pd.DataFrame:
    """One row per market with headline forecast-quality metrics."""
    rows = []
    for market, stats in (report.get("markets") or {}).items():
        if not stats.get("samples"):
            continue
        rows.append(
            {
                "Market": MARKET_LABELS.get(market, market),
                "Samples": stats["samples"],
                "Base rate (over)": stats.get("base_rate_over"),
                "Brier": stats.get("brier"),
                "Skill vs base rate": stats.get("brier_skill_vs_base_rate"),
                "Log loss": stats.get("log_loss"),
                "MAE (projection)": stats.get("mean_abs_error"),
            }
        )
    return pd.DataFrame(rows)


def reliability_frame(report: dict[str, Any], market: str) -> pd.DataFrame:
    """Predicted vs observed over-rate per probability bucket, plus the perfect diagonal."""
    bins = ((report.get("markets") or {}).get(market) or {}).get("reliability") or []
    frame = pd.DataFrame(
        {
            "Predicted": [b["mean_predicted"] for b in bins],
            "Observed": [b["observed_rate"] for b in bins],
            "Perfect": [b["mean_predicted"] for b in bins],
            "Count": [b["count"] for b in bins],
        }
    )
    return frame.set_index("Predicted") if not frame.empty else frame


def breakdown_frame(summary: dict[str, Any], key: str) -> pd.DataFrame:
    """Rows from a summary breakdown (``by_market``, ``by_side``, ``by_edge_bucket``)."""
    rows = []
    for name, stats in (summary.get(key) or {}).items():
        if not isinstance(stats, dict) or not stats.get("bets"):
            continue
        rows.append(
            {
                "Group": MARKET_LABELS.get(name, name),
                "Bets": stats["bets"],
                "W-L-P": f"{stats.get('wins', 0)}-{stats.get('losses', 0)}-{stats.get('pushes', 0)}",
                "Win rate": stats.get("win_rate"),
                "ROI": stats.get("roi"),
                "Profit (u)": stats.get("profit_units"),
                "Avg edge": stats.get("avg_edge"),
                "Avg model p": stats.get("avg_model_prob"),
            }
        )
    return pd.DataFrame(rows)


def bankroll_frame(curve: Any) -> pd.DataFrame:
    """Daily bankroll curve indexed by date; empty for missing or malformed input."""
    if not isinstance(curve, list) or not curve:
        return pd.DataFrame()
    frame = pd.DataFrame(curve)
    if not {"date", "bankroll"} <= set(frame.columns):
        return pd.DataFrame()
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")[["bankroll"]]
