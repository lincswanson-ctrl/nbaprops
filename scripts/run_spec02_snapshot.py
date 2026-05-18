"""Run one full Spec 02 injury scrape/load/validation cycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from data_pipeline.loaders.injuries import InjuryLoader
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.injuries import ESPNScraper, RotowireScraper, InjuryRecord
from scripts.validate_injury_sources import validate_sources

logger = get_logger(__name__)


async def _scrape() -> tuple[list[InjuryRecord], list[InjuryRecord]]:
    rotowire = RotowireScraper()
    espn = ESPNScraper()
    rw_content = await rotowire.fetch()
    es_content = await espn.fetch()
    return rotowire.parse(rw_content), espn.parse(es_content)


def main() -> int:
    """Run one injury snapshot cycle and validate disagreement."""

    rw_records, es_records = asyncio.run(_scrape())
    loader = InjuryLoader()
    rw_summary = loader.load_snapshot(rw_records)
    es_summary = loader.load_snapshot(es_records)
    total, disagreements = validate_sources(datetime.now(timezone.utc))
    rate = (disagreements / total * 100.0) if total else 0.0
    logger.info(
        "spec02_cycle_complete",
        rotowire_total=rw_summary.total_records,
        espn_total=es_summary.total_records,
        rotowire_inserted=rw_summary.inserted,
        espn_inserted=es_summary.inserted,
        disagreement_rate=round(rate, 2),
    )
    return 0 if rate < 5.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
