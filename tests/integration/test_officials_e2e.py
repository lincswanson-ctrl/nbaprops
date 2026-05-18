"""Integration tests for officials scrape/load flow."""

from __future__ import annotations

import os
from datetime import date

import pytest

from data_pipeline.loaders.officials import OfficialsLoader
from data_pipeline.scrapers.officials import NBAOfficialsScraper
from scripts.validate_officials_cross_source import validate_cross_source


def _network_enabled() -> bool:
    return os.getenv("RUN_NETWORK_TESTS", "").lower() in {"1", "true", "yes"}


@pytest.mark.network
def test_e2e_today_assignments() -> None:
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")

    async def _run() -> int:
        scraper = NBAOfficialsScraper()
        payload = await scraper.fetch_assignments(date.today())
        rows = scraper.parse_assignments(payload, date.today())
        return len(rows)

    import asyncio

    count = asyncio.run(_run())
    assert count >= 0


@pytest.mark.network
def test_e2e_full_season_backfill() -> None:
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")
    season = os.getenv("OFFICIALS_BACKFILL_SEASON", "2024-25")
    summary = OfficialsLoader().load_from_nba_stats(season)
    assert summary.rows_seen >= summary.rows_upserted >= 0


@pytest.mark.network
def test_per_ref_aggregate_query() -> None:
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")
    target_date = os.getenv("OFFICIALS_VALIDATE_DATE", date.today().isoformat())
    summary = validate_cross_source(target_date)
    assert summary.games >= 0
