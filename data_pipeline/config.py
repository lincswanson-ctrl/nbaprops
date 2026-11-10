"""Runtime settings for data pipeline services."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str
    database_url_transaction: str | None = None
    raw_storage_path: Path = Path("./data/raw")
    nba_stats_rate_limit_per_min: int = 30
    odds_poll_interval_far_minutes: int = 10
    odds_poll_interval_near_seconds: int = 60
    odds_poll_interval_closing_seconds: int = 30
    odds_poll_closing_window_minutes: int = 5
    odds_poll_near_window_minutes: int = 30
    odds_poll_start_window_hours: int = 4
    discord_webhook_url: str | None = None
    # Pregame model: "baseline_v1" (spec 05) or "naive_v1" (legacy placeholder).
    pregame_model_version: Literal["baseline_v1", "naive_v1"] = "baseline_v1"
    # Spec 08: per-market model/market blend weights applied to baseline_v1 probabilities.
    blend_weights_path: Path = Path("config/blend_weights.yaml")
    # FanDuel scraper: SBAPI host must match the licensed state subdomain from discovery.
    # Override via FANDUEL_SBAPI_HOST when geolocation resolves elsewhere.
    fanduel_sbapi_host: str = "sbapi.nj.sportsbook.fanduel.com"
    # FanDuel SMP host for fixed-odds pricing (`getMarketPrices`). Pair with SBAPI state.
    fanduel_smp_host: str = "smp.nj.sportsbook.fanduel.com"
    # Bootstrap key `_ak` observed on sportsbook.fanduel.com XHR (same family as in-play service).
    fanduel_api_key: str = "FhMFpcPWXMeyZxOx"
    # BetMGM: base URL and subdivision must correspond to the same licensed state.
    betmgm_base_url: str = "https://www.nj.betmgm.com"
    betmgm_subdivision: str = "US-NewJersey"
    betmgm_access_id: str = "ZTllNjllODUtOWQwNS00YmU4LWE4NTEtZGZjOTkzMGM5OWU4"
    # DraftKings: comma-separated leagueSubcategory IDs tried in order for player_points only.
    # Leave empty to use the built-in default (currently milestones-heavy NBA Points tab id).
    draftkings_player_points_subcategory_ids: str = ""
    # DraftKings eventSubcategory IDs for combo markets (event endpoint classic O/U props).
    # Leave empty to keep the combos disabled (scraper will skip those markets cleanly).
    draftkings_event_subcat_player_points_rebounds_assists: str = ""
    draftkings_event_subcat_player_points_rebounds: str = ""
    draftkings_event_subcat_player_points_assists: str = ""
    draftkings_event_subcat_player_rebounds_assists: str = ""
    odds_allow_synthetic_games: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()
