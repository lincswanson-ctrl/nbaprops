from __future__ import annotations

from typing import Any

from data_pipeline.scrapers.odds.draftkings import _extract_selection_price


def test_draftkings_extract_price_unicode_minus() -> None:
    selection: dict[str, Any] = {"displayOdds": {"american": "−110"}}
    assert _extract_selection_price(selection) == -110


def test_draftkings_extract_price_plus_string() -> None:
    selection: dict[str, Any] = {"displayOdds": {"american": "+120"}}
    assert _extract_selection_price(selection) == 120

