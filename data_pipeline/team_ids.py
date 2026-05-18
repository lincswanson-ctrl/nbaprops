"""Normalize NBA team identifiers across TEXT/BIGINT/Decimal representations."""

from __future__ import annotations

from decimal import Decimal


def normalize_team_id(value: object) -> str:
    """Normalize team identifiers from Postgres (TEXT vs legacy BIGINT)."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value))
    if isinstance(value, Decimal):
        return str(int(value))
    return str(value).strip()
