"""Run Spec 01 backfill flow across NBA seasons."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
import json

from data_pipeline.loaders.nba_stats import NBAStatsLoader
from data_pipeline.logging import get_logger
from data_pipeline.seasons import current_nba_season
from data_pipeline.scrapers.nba_stats import NBAStatsScraper, _extract_game_ids

logger = get_logger(__name__)


def _season_labels(start_season: str, end_season: str) -> list[str]:
    start_year = int(start_season.split("-")[0])
    end_year = int(end_season.split("-")[0])
    labels: list[str] = []
    for year in range(start_year, end_year + 1):
        labels.append(f"{year}-{str(year + 1)[-2:]}")
    return labels


async def _scrape_season(season: str) -> None:
    async with NBAStatsScraper() as scraper:
        game_finder = await scraper.fetch_leaguegamefinder(season)
        payload = json.loads(game_finder.raw_file_path.read_text(encoding="utf-8"))
        for game_id in _extract_game_ids(payload):
            await scraper.fetch_game_bundle(season, game_id)
        # pull one recent scoreboard snapshot for schedule consistency
        await scraper.fetch_scoreboard(season, date.today())


def main() -> int:
    """CLI entrypoint for full Spec 01 backfill run."""

    parser = argparse.ArgumentParser(description="Run Spec 01 backfill")
    parser.add_argument("--start-season", default="2020-21")
    parser.add_argument("--end-season", default=current_nba_season())
    args = parser.parse_args()
    seasons = _season_labels(args.start_season, args.end_season)
    loader = NBAStatsLoader()
    for season in seasons:
        logger.info("spec01_backfill_scrape_start", season=season)
        asyncio.run(_scrape_season(season))
        logger.info("spec01_backfill_load_start", season=season)
        games_upserted = loader.load_season(season)
        logger.info("spec01_backfill_load_complete", season=season, games_upserted=games_upserted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
