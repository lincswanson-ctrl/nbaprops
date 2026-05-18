"""Resolve bookmaker events to internal game IDs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from data_pipeline.config import get_settings
from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.odds.base import normalize_team_abbr

logger = get_logger(__name__)


@dataclass(frozen=True)
class ResolvedEvent:
    """Resolution output for one bookmaker event."""

    game_id: str
    home_team_id: str
    away_team_id: str


def resolve_event_to_game_id(
    *,
    bookmaker_key: str,
    bookmaker_event_id: str,
    home_team_name: str,
    away_team_name: str,
    commence_time: datetime,
) -> ResolvedEvent | None:
    """Resolve bookmaker event metadata to a `games.game_id`."""
    cached = _read_cached_mapping(bookmaker_key=bookmaker_key, bookmaker_event_id=bookmaker_event_id)
    if cached is not None:
        return cached
    try:
        home_abbr = normalize_team_abbr(home_team_name)
        away_abbr = normalize_team_abbr(away_team_name)
    except ValueError:
        logger.warning(
            "odds_event_resolution_unknown_team",
            bookmaker_key=bookmaker_key,
            bookmaker_event_id=bookmaker_event_id,
            home_team_name=home_team_name,
            away_team_name=away_team_name,
        )
        return None
    candidate = _query_game(home_abbr=home_abbr, away_abbr=away_abbr, commence_time=commence_time)
    if candidate is None:
        if get_settings().odds_allow_synthetic_games:
            synthetic = _upsert_synthetic_game_and_mapping(
                bookmaker_key=bookmaker_key,
                bookmaker_event_id=bookmaker_event_id,
                home_abbr=home_abbr,
                away_abbr=away_abbr,
                commence_time=commence_time,
            )
            if synthetic is not None:
                return synthetic
        logger.warning(
            "odds_event_resolution_not_found",
            bookmaker_key=bookmaker_key,
            bookmaker_event_id=bookmaker_event_id,
            home_team=home_abbr,
            away_team=away_abbr,
            commence_time=commence_time.isoformat(),
        )
        return None
    _cache_mapping(
        bookmaker_key=bookmaker_key,
        bookmaker_event_id=bookmaker_event_id,
        commence_time=commence_time,
        home_team_id=candidate.home_team_id,
        away_team_id=candidate.away_team_id,
        game_id=candidate.game_id,
    )
    return candidate


def _read_cached_mapping(*, bookmaker_key: str, bookmaker_event_id: str) -> ResolvedEvent | None:
    query = """
        SELECT game_id, home_team_id, away_team_id
        FROM bookmaker_event_mapping
        WHERE bookmaker_key = %(bookmaker_key)s AND bookmaker_event_id = %(bookmaker_event_id)s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                {"bookmaker_key": bookmaker_key, "bookmaker_event_id": bookmaker_event_id},
            )
            row = cur.fetchone()
    if row is None:
        return None
    return ResolvedEvent(
        game_id=str(row["game_id"]),
        home_team_id=str(row["home_team_id"]),
        away_team_id=str(row["away_team_id"]),
    )


def _query_game(*, home_abbr: str, away_abbr: str, commence_time: datetime) -> ResolvedEvent | None:
    begin = commence_time.astimezone(timezone.utc) - timedelta(hours=24)
    end = commence_time.astimezone(timezone.utc) + timedelta(hours=24)
    query = """
        SELECT g.game_id, g.home_team_id, g.away_team_id
        FROM games g
        JOIN teams home_t ON home_t.team_id = g.home_team_id
        JOIN teams away_t ON away_t.team_id = g.away_team_id
        WHERE home_t.abbreviation = %(home_abbr)s
          AND away_t.abbreviation = %(away_abbr)s
          AND g.date BETWEEN %(start_date)s::date AND %(end_date)s::date
        ORDER BY ABS(EXTRACT(EPOCH FROM (%(commence)s::timestamptz - g.date::timestamptz))) ASC
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                {
                    "home_abbr": home_abbr,
                    "away_abbr": away_abbr,
                    "start_date": begin.date().isoformat(),
                    "end_date": end.date().isoformat(),
                    "commence": commence_time.astimezone(timezone.utc).isoformat(),
                },
            )
            row = cur.fetchone()
    if row is None:
        return None
    return ResolvedEvent(
        game_id=str(row["game_id"]),
        home_team_id=str(row["home_team_id"]),
        away_team_id=str(row["away_team_id"]),
    )


def _cache_mapping(
    *,
    bookmaker_key: str,
    bookmaker_event_id: str,
    game_id: str,
    home_team_id: str,
    away_team_id: str,
    commence_time: datetime,
) -> None:
    query = """
        INSERT INTO bookmaker_event_mapping (
            bookmaker_key, bookmaker_event_id, game_id, home_team_id, away_team_id, commence_time
        ) VALUES (
            %(bookmaker_key)s, %(bookmaker_event_id)s, %(game_id)s, %(home_team_id)s, %(away_team_id)s, %(commence_time)s
        )
        ON CONFLICT (bookmaker_key, bookmaker_event_id) DO UPDATE SET
            game_id = EXCLUDED.game_id,
            home_team_id = EXCLUDED.home_team_id,
            away_team_id = EXCLUDED.away_team_id,
            commence_time = EXCLUDED.commence_time
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                {
                    "bookmaker_key": bookmaker_key,
                    "bookmaker_event_id": bookmaker_event_id,
                    "game_id": game_id,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "commence_time": commence_time.astimezone(timezone.utc).isoformat(),
                },
            )


def _lookup_team_id(abbr: str) -> str | None:
    query = "SELECT team_id FROM teams WHERE abbreviation = %(abbr)s LIMIT 1"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, {"abbr": abbr})
            row = cur.fetchone()
    if row is None:
        return None
    return str(row["team_id"])


def _infer_nba_season(commence_time: datetime) -> str:
    date_only = commence_time.astimezone(timezone.utc).date()
    if date_only.month >= 7:
        start_year = date_only.year
    else:
        start_year = date_only.year - 1
    end_year = start_year + 1
    return f"{start_year}-{str(end_year)[-2:]}"


def _upsert_synthetic_game_and_mapping(
    *,
    bookmaker_key: str,
    bookmaker_event_id: str,
    home_abbr: str,
    away_abbr: str,
    commence_time: datetime,
) -> ResolvedEvent | None:
    game_id = f"{bookmaker_key}_{bookmaker_event_id}"
    home_team_id = _lookup_team_id(home_abbr)
    away_team_id = _lookup_team_id(away_abbr)
    if home_team_id is None or away_team_id is None:
        logger.warning(
            "odds_event_resolution_synthetic_game_missing_team",
            bookmaker_key=bookmaker_key,
            bookmaker_event_id=bookmaker_event_id,
            home_abbr=home_abbr,
            away_abbr=away_abbr,
        )
        return None

    game_date_local = commence_time.astimezone(ZoneInfo("America/New_York")).date()
    season = _infer_nba_season(commence_time)
    season_type = _infer_season_type(commence_time)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO games (game_id, date, season, season_type, home_team_id, away_team_id)
                VALUES (%(game_id)s, %(date)s, %(season)s, %(season_type)s, %(home_team_id)s, %(away_team_id)s)
                ON CONFLICT (game_id) DO UPDATE SET
                    date = EXCLUDED.date,
                    season = EXCLUDED.season,
                    season_type = EXCLUDED.season_type,
                    home_team_id = EXCLUDED.home_team_id,
                    away_team_id = EXCLUDED.away_team_id,
                    updated_at = NOW()
                """,
                {
                    "game_id": game_id,
                    "date": game_date_local.isoformat(),
                    "season": season,
                    "season_type": season_type,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                },
            )
            cur.execute(
                """
                INSERT INTO bookmaker_event_mapping (
                    bookmaker_key, bookmaker_event_id, game_id, home_team_id, away_team_id, commence_time
                ) VALUES (
                    %(bookmaker_key)s, %(bookmaker_event_id)s, %(game_id)s, %(home_team_id)s, %(away_team_id)s, %(commence_time)s
                )
                ON CONFLICT (bookmaker_key, bookmaker_event_id) DO UPDATE SET
                    game_id = EXCLUDED.game_id,
                    home_team_id = EXCLUDED.home_team_id,
                    away_team_id = EXCLUDED.away_team_id,
                    commence_time = EXCLUDED.commence_time
                """,
                {
                    "bookmaker_key": bookmaker_key,
                    "bookmaker_event_id": bookmaker_event_id,
                    "game_id": game_id,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "commence_time": commence_time.astimezone(timezone.utc).isoformat(),
                },
            )

    logger.info(
        "odds_event_resolution_synthetic_game_created",
        bookmaker_key=bookmaker_key,
        bookmaker_event_id=bookmaker_event_id,
        game_id=game_id,
        season=season,
        season_type=season_type,
        date=game_date_local.isoformat(),
        home_team_id=home_team_id,
        away_team_id=away_team_id,
    )
    return ResolvedEvent(game_id=game_id, home_team_id=home_team_id, away_team_id=away_team_id)


def _infer_season_type(commence_time: datetime) -> str:
    """Infer season type for synthetic games when stats.nba.com schedule is unavailable."""
    date_only = commence_time.astimezone(timezone.utc).date()
    if date_only.month == 4 and date_only.day <= 20:
        return "play_in"
    if date_only.month in {4, 5, 6}:
        return "playoffs"
    return "regular_season"
