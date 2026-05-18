"""Endpoint definitions for stats.nba.com API calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

NBA_STATS_HEADERS: dict[str, str] = {
    "Host": "stats.nba.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}


@dataclass(frozen=True)
class Endpoint:
    """Immutable endpoint metadata."""

    name: str
    path: str


ENDPOINTS: dict[str, Endpoint] = {
    "leaguegamefinder": Endpoint("leaguegamefinder", "leaguegamefinder"),
    "boxscoretraditionalv2": Endpoint("boxscoretraditionalv2", "boxscoretraditionalv2"),
    "boxscoreadvancedv2": Endpoint("boxscoreadvancedv2", "boxscoreadvancedv2"),
    "boxscoreplayertrackv2": Endpoint("boxscoreplayertrackv2", "boxscoreplayertrackv2"),
    "playergamelogs": Endpoint("playergamelogs", "playergamelogs"),
    "commonplayerinfo": Endpoint("commonplayerinfo", "commonplayerinfo"),
    "leaguedashplayerstats": Endpoint("leaguedashplayerstats", "leaguedashplayerstats"),
    "boxscorematchupsv3": Endpoint("boxscorematchupsv3", "boxscorematchupsv3"),
    "scoreboardv2": Endpoint("scoreboardv2", "scoreboardv2"),
}

BASE_URL = "https://stats.nba.com/stats"


def season_to_date_range(season: str) -> tuple[str, str]:
    """Convert season label to an approximate date range."""

    start_year, end_suffix = season.split("-")
    end_year = int(f"20{end_suffix}")
    start = date(int(start_year), 10, 1)
    end = date(end_year, 7, 1)
    return start.isoformat(), end.isoformat()
