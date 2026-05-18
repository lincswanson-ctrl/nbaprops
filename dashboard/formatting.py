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


_MARKET_LABELS: dict[str, str] = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_steals": "Steals",
    "player_blocks": "Blocks",
    "player_threes": "3-Pointers",
    "player_turnovers": "Turnovers",
    "player_steals_blocks": "Steals + Blocks",
    "player_points_rebounds": "Pts + Reb",
    "player_points_assists": "Pts + Ast",
    "player_rebounds_assists": "Reb + Ast",
    "player_points_rebounds_assists": "Pts + Reb + Ast",
}


def display_market(market: str) -> str:
    key = str(market).strip().lower()
    if key in _MARKET_LABELS:
        return _MARKET_LABELS[key]
    # fallback: strip player_ prefix and clean underscores
    return key.replace("player_", "").replace("_", " ").title()
