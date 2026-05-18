"""Common models and normalization helpers for odds scrapers."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

CanonicalMarket = Literal[
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_points_rebounds_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_steals",
    "player_blocks",
    "player_steals_blocks",
    "player_turnovers",
]

_CANONICAL_MARKETS: tuple[CanonicalMarket, ...] = (
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_points_rebounds_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_steals",
    "player_blocks",
    "player_steals_blocks",
    "player_turnovers",
)

_MARKET_ALIASES: dict[str, CanonicalMarket] = {
    "points": "player_points",
    "player points": "player_points",
    "player points o/u": "player_points",
    "player_points": "player_points",
    "rebounds": "player_rebounds",
    "player rebounds": "player_rebounds",
    "player_rebounds": "player_rebounds",
    "assists": "player_assists",
    "player assists": "player_assists",
    "player_assists": "player_assists",
    "threes": "player_threes",
    "3-pointers": "player_threes",
    "player threes": "player_threes",
    "player_threes": "player_threes",
    "pra": "player_points_rebounds_assists",
    "pts + reb + ast": "player_points_rebounds_assists",
    "points + rebounds + assists": "player_points_rebounds_assists",
    "player points rebounds assists": "player_points_rebounds_assists",
    "player_points_rebounds_assists": "player_points_rebounds_assists",
    "p+r": "player_points_rebounds",
    "pts + rebs": "player_points_rebounds",
    "player_points_rebounds": "player_points_rebounds",
    "p+a": "player_points_assists",
    "pts + ast": "player_points_assists",
    "player_points_assists": "player_points_assists",
    "r+a": "player_rebounds_assists",
    "reb + ast": "player_rebounds_assists",
    "player_rebounds_assists": "player_rebounds_assists",
    "steals": "player_steals",
    "player steals": "player_steals",
    "player_steals": "player_steals",
    "blocks": "player_blocks",
    "player blocks": "player_blocks",
    "player_blocks": "player_blocks",
    "steals + blocks": "player_steals_blocks",
    "stl + blk": "player_steals_blocks",
    "player_steals_blocks": "player_steals_blocks",
    "turnovers": "player_turnovers",
    "player turnovers": "player_turnovers",
    "player_turnovers": "player_turnovers",
}

_TEAM_ALIASES: dict[str, str] = {
    "atlanta hawks": "ATL",
    "hawks": "ATL",
    "boston celtics": "BOS",
    "celtics": "BOS",
    "brooklyn nets": "BKN",
    "nets": "BKN",
    "charlotte hornets": "CHA",
    "hornets": "CHA",
    "chicago bulls": "CHI",
    "bulls": "CHI",
    "cleveland cavaliers": "CLE",
    "cavaliers": "CLE",
    "dallas mavericks": "DAL",
    "mavericks": "DAL",
    "denver nuggets": "DEN",
    "nuggets": "DEN",
    "detroit pistons": "DET",
    "pistons": "DET",
    "golden state warriors": "GSW",
    "warriors": "GSW",
    "houston rockets": "HOU",
    "rockets": "HOU",
    "indiana pacers": "IND",
    "pacers": "IND",
    "la clippers": "LAC",
    "los angeles clippers": "LAC",
    "clippers": "LAC",
    "la lakers": "LAL",
    "los angeles lakers": "LAL",
    "lakers": "LAL",
    "memphis grizzlies": "MEM",
    "grizzlies": "MEM",
    "miami heat": "MIA",
    "heat": "MIA",
    "milwaukee bucks": "MIL",
    "bucks": "MIL",
    "minnesota timberwolves": "MIN",
    "timberwolves": "MIN",
    "new orleans pelicans": "NOP",
    "pelicans": "NOP",
    "new york knicks": "NYK",
    "knicks": "NYK",
    "okc thunder": "OKC",
    "oklahoma city thunder": "OKC",
    "thunder": "OKC",
    "orlando magic": "ORL",
    "magic": "ORL",
    "philadelphia 76ers": "PHI",
    "philly 76ers": "PHI",
    "76ers": "PHI",
    "phoenix suns": "PHX",
    "suns": "PHX",
    "portland trail blazers": "POR",
    "trail blazers": "POR",
    "sacramento kings": "SAC",
    "kings": "SAC",
    "san antonio spurs": "SAS",
    "sa spurs": "SAS",
    "spurs": "SAS",
    "toronto raptors": "TOR",
    "raptors": "TOR",
    "utah jazz": "UTA",
    "jazz": "UTA",
    "washington wizards": "WAS",
    "wizards": "WAS",
}


class OddsEvent(BaseModel):
    """Bookmaker event metadata."""

    model_config = ConfigDict(extra="forbid")
    bookmaker_key: str
    bookmaker_event_id: str
    home_team_name: str
    away_team_name: str
    commence_time: datetime


class PropOffer(BaseModel):
    """Normalized player prop offer."""

    model_config = ConfigDict(extra="forbid")
    bookmaker_key: str
    bookmaker_event_id: str
    home_team_name: str
    away_team_name: str
    commence_time: datetime
    raw_player_name: str
    market: CanonicalMarket
    line: float
    over_price: int
    under_price: int
    last_update: datetime | None
    scraped_at: datetime
    raw_response_path: str | None = None
    explicit_over_under: bool = False


class MoneylineOffer(BaseModel):
    """Normalized team moneyline offer."""

    model_config = ConfigDict(extra="forbid")
    bookmaker_key: str
    bookmaker_event_id: str
    home_team_name: str
    away_team_name: str
    commence_time: datetime
    team: str
    moneyline_odds: int
    scraped_at: datetime
    last_update: datetime | None = None
    raw_response_path: str | None = None


def canonical_market_names() -> tuple[CanonicalMarket, ...]:
    """Return all canonical market names."""
    return _CANONICAL_MARKETS


def normalize_market_name(name: str) -> CanonicalMarket:
    """Normalize raw market text to canonical market key."""
    token = " ".join(name.strip().lower().replace("_", " ").split())
    if token not in _MARKET_ALIASES:
        raise ValueError(f"Unknown market name: {name}")
    return _MARKET_ALIASES[token]


def normalize_team_abbr(name: str) -> str:
    """Normalize a team name/alias to three-letter abbreviation."""
    token = " ".join(name.strip().lower().split())
    if token not in _TEAM_ALIASES:
        # DraftKings sometimes prefixes team names with abbreviation, e.g.
        # "CLE Cavaliers" or "DET Pistons".
        stripped = re.sub(r"^[a-z]{2,4}\s+", "", token)
        if stripped and stripped in _TEAM_ALIASES:
            return _TEAM_ALIASES[stripped]
        raise ValueError(f"Unknown NBA team alias: {name}")
    return _TEAM_ALIASES[token]
