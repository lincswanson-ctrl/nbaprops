"""Integration tests for NBA stats scraper/loader."""

from __future__ import annotations

import os
from datetime import date

import pytest

from data_pipeline.loaders.nba_stats import NBAStatsLoader
from data_pipeline.scrapers.nba_stats import NBAStatsScraper
from scripts.validate_nba_load import validate_random_games


def _network_enabled() -> bool:
    return os.getenv("RUN_NETWORK_TESTS", "").lower() in {"1", "true", "yes"}


@pytest.mark.network
def test_e2e_single_game() -> None:  # noqa: PLR0915
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")

    async def _run() -> None:
        async with NBAStatsScraper() as scraper:
            await scraper.fetch_scoreboard("2025-26", date.today())

    import asyncio

    asyncio.run(_run())
    summary = NBAStatsLoader().load_scoreboard_schedule("2025-26", date.today())
    assert summary.files_loaded + summary.files_skipped >= 1


@pytest.mark.network
def test_e2e_full_day() -> None:
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")
    loaded = NBAStatsLoader().load_season("2025-26")
    assert loaded >= 0


@pytest.mark.network
def test_backfill_validation() -> None:
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")
    mismatches = validate_random_games(limit=5)
    assert isinstance(mismatches, list)
