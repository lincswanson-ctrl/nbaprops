"""Formatting helpers."""

from __future__ import annotations

from typing import Any


def pct(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{v * 100:.1f}%"


def money(value: float) -> str:
    return f"${value:,.2f}"


def display_market(market: str) -> str:
    m = str(market).strip().lower().replace("player_", "")
    return m.replace("_", " ").title()
