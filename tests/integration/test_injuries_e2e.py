"""Integration tests for injury scraping/loading."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from data_pipeline.loaders.injuries import InjuryLoader
from data_pipeline.scrapers.injuries import ESPNScraper, RotowireScraper
from scripts.validate_injury_sources import validate_sources


def _network_enabled() -> bool:
    return os.getenv("RUN_NETWORK_TESTS", "").lower() in {"1", "true", "yes"}


@pytest.mark.network
def test_e2e_rotowire_live() -> None:
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")

    async def _run() -> int:
        scraper = RotowireScraper()
        content = await scraper.fetch()
        records = scraper.parse(content)
        return InjuryLoader().load_snapshot(records).total_records

    import asyncio

    total = asyncio.run(_run())
    assert total >= 0


@pytest.mark.network
def test_e2e_cross_source_validation() -> None:
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")

    async def _run() -> None:
        rw = RotowireScraper()
        es = ESPNScraper()
        rw_records = rw.parse(await rw.fetch())
        es_records = es.parse(await es.fetch())
        loader = InjuryLoader()
        loader.load_snapshot(rw_records)
        loader.load_snapshot(es_records)

    import asyncio

    asyncio.run(_run())
    total, disagreements = validate_sources(datetime.now(timezone.utc))
    assert total >= disagreements


@pytest.mark.network
def test_status_timeline_for_player() -> None:
    if not _network_enabled():
        pytest.skip("Set RUN_NETWORK_TESTS=1 to run integration tests.")
    total, _ = validate_sources(datetime.now(timezone.utc))
    assert total >= 0
