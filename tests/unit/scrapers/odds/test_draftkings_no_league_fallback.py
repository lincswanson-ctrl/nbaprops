from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from data_pipeline.scrapers.odds.base import OddsEvent
from data_pipeline.scrapers.odds.draftkings import DraftKingsScraper


@pytest.mark.network
def test_placeholder() -> None:
    """Kept to match suite conventions (no live DK calls in unit tests)."""
    assert True


def test_draftkings_skips_market_without_event_subcategory(monkeypatch: Any) -> None:
    """Markets with no configured eventSubcategory id should not probe leagueSubcategory variants."""

    async def _should_not_be_called(*_: Any, **__: Any) -> Any:
        raise AssertionError("leagueSubcategory fallback should not run for unsupported markets")

    monkeypatch.setattr(DraftKingsScraper, "_fetch_market_payload", _should_not_be_called, raising=True)
    monkeypatch.setattr(DraftKingsScraper, "_fetch_market_payload_variant", _should_not_be_called, raising=True)
    monkeypatch.setattr(DraftKingsScraper, "_fetch_event_subcat_payload", _should_not_be_called, raising=True)

    event = OddsEvent(
        bookmaker_key="draftkings",
        bookmaker_event_id="34134193",
        home_team_name="DET Pistons",
        away_team_name="CLE Cavaliers",
        commence_time=datetime(2026, 5, 14, 0, 10, tzinfo=timezone.utc),
    )

    async def _run() -> None:
        async with DraftKingsScraper() as scraper:
            offers = await scraper.fetch_player_props(event, "player_turnovers")
            assert offers == []

    asyncio.run(_run())

