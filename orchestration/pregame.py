"""Pregame pipeline orchestrator backed by Postgres tables."""

from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime
import os
from pathlib import Path
from statistics import median
from typing import Any

import httpx
import structlog

from data_pipeline.db import get_connection
from data_pipeline.loaders.injuries import InjuryLoader
from data_pipeline.loaders.nba_stats import NBAStatsLoader
from data_pipeline.scrapers.injuries import ESPNScraper, RotowireScraper
from data_pipeline.scrapers.nba_stats import NBAStatsScraper
from data_pipeline.scrapers.odds.__main__ import run_once as run_odds_once
from data_pipeline.scrapers.odds.__main__ import infer_nba_season, refresh_today_schedule
from data_pipeline.scrapers.odds.base import canonical_market_names, normalize_team_abbr
from data_pipeline.scrapers.odds.betmgm import BetMGMScraper
from data_pipeline.scrapers.odds.draftkings import DraftKingsScraper

logger = structlog.get_logger(__name__)

EDGE_THRESHOLD = 0.02
EDGE_SANITY_BOUND = 0.50
MODEL_VERSION = "naive_v1"
STALE_ODDS_MINUTES = 720
MAX_PICKS_PER_PLAYER = 2
MAX_PICKS_PER_GAME = 6
MARKET_EDGE_THRESHOLDS: dict[str, float] = {
    "player_points": 0.05,
    "player_rebounds": 0.05,
    "player_assists": 0.05,
    "player_threes": 0.05,
    "player_points_rebounds_assists": 0.06,
    "player_points_rebounds": 0.06,
    "player_points_assists": 0.06,
    "player_rebounds_assists": 0.06,
    "player_blocks": 0.07,
    "player_steals": 0.07,
    "player_steals_blocks": 0.07,
}


@dataclass(frozen=True)
class StepResult:
    """Serialized step result for logging and summaries."""

    name: str
    details: dict[str, Any]


def run_ingest(target_date: date, season: str) -> StepResult:
    """Run existing NBA/injury scrape+load components for the target date."""
    _ensure_pregame_tables()
    games_loaded = _ingest_nba_schedule(target_date, season)
    injury_records = _ingest_injuries()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM games WHERE date = %(d)s", {"d": target_date})
            game_count = int(cur.fetchone()["n"])
            if game_count == 0:
                seeded = _seed_games_from_odds_events(target_date, season)
                if seeded == 0:
                    seeded = _seed_games_from_espn_schedule(target_date, season)
                if seeded > 0:
                    cur.execute("SELECT COUNT(*) AS n FROM games WHERE date = %(d)s", {"d": target_date})
                    game_count = int(cur.fetchone()["n"])
                if game_count == 0:
                    logger.warning("pregame_no_games_for_date", date=target_date.isoformat())
    return StepResult(
        name="1_ingest",
        details={
            "games_loaded": games_loaded,
            "injury_records": injury_records,
            "games_available": game_count,
        },
    )


def _seed_games_from_odds_events(target_date: date, season: str) -> int:
    async def _collect_events() -> list[tuple[str, str, str, datetime]]:
        records: list[tuple[str, str, str, datetime]] = []
        for scraper_cls, book in ((DraftKingsScraper, "draftkings"), (BetMGMScraper, "betmgm")):
            try:
                async with scraper_cls() as scraper:
                    events = await scraper.list_events()
                    for event in events:
                        if event.commence_time.date() != target_date:
                            continue
                        records.append((book, event.home_team_name, event.away_team_name, event.commence_time))
            except Exception as exc:
                logger.warning("pregame_seed_games_from_odds_failed", book=book, error=str(exc))
        return records

    events = asyncio.run(_collect_events())
    if not events:
        return 0
    seeded = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for book, home_team_name, away_team_name, commence_time in events:
                try:
                    home_abbr = normalize_team_abbr(home_team_name)
                    away_abbr = normalize_team_abbr(away_team_name)
                except ValueError:
                    continue
                cur.execute("SELECT team_id FROM teams WHERE abbreviation = %(abbr)s", {"abbr": home_abbr})
                home = cur.fetchone()
                cur.execute("SELECT team_id FROM teams WHERE abbreviation = %(abbr)s", {"abbr": away_abbr})
                away = cur.fetchone()
                if home is None or away is None:
                    continue
                game_id = f"{book}_{home_abbr}_{away_abbr}_{target_date.isoformat()}"
                seeded += _insert_seeded_game(
                    cur=cur,
                    game_id=game_id,
                    target_date=target_date,
                    season=season,
                    home_team_id=str(home["team_id"]),
                    away_team_id=str(away["team_id"]),
                )
    return seeded


def _seed_games_from_espn_schedule(target_date: date, season: str) -> int:
    try:
        events = _fetch_espn_scoreboard_events(target_date)
    except Exception as exc:
        logger.warning("pregame_seed_games_from_espn_failed", error=str(exc))
        return 0
    if not events:
        return 0
    seeded = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for home_name, away_name in events:
                try:
                    home_abbr = normalize_team_abbr(home_name)
                    away_abbr = normalize_team_abbr(away_name)
                except ValueError:
                    continue
                cur.execute("SELECT team_id FROM teams WHERE abbreviation = %(abbr)s", {"abbr": home_abbr})
                home = cur.fetchone()
                cur.execute("SELECT team_id FROM teams WHERE abbreviation = %(abbr)s", {"abbr": away_abbr})
                away = cur.fetchone()
                if home is None or away is None:
                    continue
                game_id = f"espn_{home_abbr}_{away_abbr}_{target_date.isoformat()}"
                seeded += _insert_seeded_game(
                    cur=cur,
                    game_id=game_id,
                    target_date=target_date,
                    season=season,
                    home_team_id=str(home["team_id"]),
                    away_team_id=str(away["team_id"]),
                )
    logger.info("pregame_seed_games_from_espn_complete", date=target_date.isoformat(), seeded=seeded)
    return seeded


def _fetch_espn_scoreboard_events(target_date: date) -> list[tuple[str, str]]:
    dates = target_date.strftime("%Y%m%d")
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    response = httpx.get(url, params={"dates": dates}, timeout=20.0)
    response.raise_for_status()
    payload = response.json()
    return _extract_espn_event_pairs(payload)


def _extract_espn_event_pairs(payload: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for event in payload.get("events", []):
        competitions = event.get("competitions", [])
        if not competitions:
            continue
        competitors = competitions[0].get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        home_name = str(home.get("team", {}).get("displayName", "")).strip()
        away_name = str(away.get("team", {}).get("displayName", "")).strip()
        if home_name and away_name:
            pairs.append((home_name, away_name))
    return pairs


def _insert_seeded_game(
    *,
    cur: Any,
    game_id: str,
    target_date: date,
    season: str,
    home_team_id: str,
    away_team_id: str,
) -> int:
    columns = _games_table_columns(cur)
    values = _seed_game_values(
        game_id=game_id,
        target_date=target_date,
        season=season,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
    )
    required = _games_required_columns_without_default(cur)
    missing_required = sorted(required.difference(values))
    if missing_required:
        logger.warning("pregame_seed_game_missing_required_columns", missing_columns=missing_required)
        return 0
    insert_columns = [column for column in values if column in columns]
    placeholders = ", ".join(f"%({column})s" for column in insert_columns)
    column_list = ", ".join(insert_columns)
    for idx, candidate in enumerate(_seed_insert_candidates(values)):
        savepoint = f"seed_game_sp_{idx}"
        cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                f"""
                INSERT INTO games ({column_list})
                VALUES ({placeholders})
                ON CONFLICT (game_id) DO NOTHING
                """,
                {column: candidate[column] for column in insert_columns},
            )
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            return int(cur.rowcount or 0)
        except Exception as exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            if "season_type" not in str(exc).lower():
                raise
    logger.warning("pregame_seed_game_all_season_type_candidates_failed", game_id=game_id)
    return 0


def _games_table_columns(cur: Any) -> set[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'games'")
    return {str(row["column_name"]) for row in cur.fetchall()}


def _games_required_columns_without_default(cur: Any) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'games'
          AND is_nullable = 'NO'
          AND column_default IS NULL
        """
    )
    return {str(row["column_name"]) for row in cur.fetchall()}


def _seed_game_values(
    *,
    game_id: str,
    target_date: date,
    season: str,
    home_team_id: str,
    away_team_id: str,
) -> dict[str, Any]:
    season_year = int(season.split("-")[0])
    return {
        "game_id": game_id,
        "date": target_date,
        "season": season,
        "season_year": season_year,
        "season_type": "Regular Season",
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_score": None,
        "away_score": None,
    }


def _seed_insert_candidates(values: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [dict(values)]
    season_type_candidates = [
        "regular",
        "REG",
        "Regular",
        "regular_season",
        "REGULAR_SEASON",
        "R",
    ]
    if "season_type" in values:
        for season_type in season_type_candidates:
            candidate = dict(values)
            candidate["season_type"] = season_type
            candidates.append(candidate)
    return candidates


def _ingest_nba_schedule(target_date: date, season: str) -> int:
    async def _fetch() -> None:
        async with NBAStatsScraper() as scraper:
            await scraper.fetch_scoreboard(season=season, game_date=target_date)

    asyncio.run(_fetch())
    summary = NBAStatsLoader().load_scoreboard_schedule(season=season, game_date=target_date)
    return int(summary.games_upserted)


def _ingest_injuries() -> int:
    async def _fetch_records() -> tuple[list[Any], list[Any]]:
        rotowire = RotowireScraper()
        espn = ESPNScraper()
        rotowire_content, espn_content = await asyncio.gather(rotowire.fetch(), espn.fetch())
        return rotowire.parse(rotowire_content), espn.parse(espn_content)

    rotowire_records, espn_records = asyncio.run(_fetch_records())
    loader = InjuryLoader()
    rotowire_summary = loader.load_snapshot(rotowire_records)
    espn_summary = loader.load_snapshot(espn_records)
    return int(rotowire_summary.inserted + espn_summary.inserted)


def run_odds(
    target_date: date,
    season: str,
    *,
    allow_empty_odds: bool = False,
    books: tuple[str, ...] = ("all",),
) -> StepResult:
    """Scrape odds, stage raw odds rows, and build normalized market rows."""
    _ensure_pregame_tables()
    used_csv_fallback = False
    try:
        _scrape_player_prop_odds(target_date, season, books=books)
    except Exception as exc:
        logger.warning("pregame_live_odds_scrape_failed", error=str(exc))
        used_csv_fallback = True
    stats = _build_odds_tables(target_date)
    if stats["games_count"] > 0 and stats["markets_ingested"] == 0 and used_csv_fallback:
        stats = _build_odds_tables_from_csv(target_date)
    if stats["games_count"] > 0 and stats["markets_ingested"] == 0:
        if not allow_empty_odds:
            raise RuntimeError(
                "Games exist for date but no odds rows were ingested. "
                "Re-run with --allow-empty-odds to continue in schedule-only mode."
            )
        logger.warning(
            "pregame_no_odds_rows_for_games",
            date=target_date.isoformat(),
            games_count=stats["games_count"],
        )
        logger.warning(
            "pregame_books_unavailable_schedule_only_mode",
            date=target_date.isoformat(),
            games_count=stats["games_count"],
            message=(
                "No sportsbook odds were ingested (likely book access/geoblocking). "
                "Continuing in schedule-only mode."
            ),
        )
    del season
    stats["used_csv_fallback"] = int(used_csv_fallback)
    stats["books"] = list(books)
    return StepResult(name="2_odds", details=stats)


def _scrape_player_prop_odds(target_date: date, season: str, *, books: tuple[str, ...]) -> None:
    _refresh_target_schedule(target_date, season)
    if books == ("all",):
        asyncio.run(run_odds_once("all", list(canonical_market_names()), refresh_schedule=False))
        return
    for book in books:
        asyncio.run(run_odds_once(book, list(canonical_market_names()), refresh_schedule=False))


def _refresh_target_schedule(target_date: date, season: str) -> None:
    today_season = infer_nba_season(date.today())
    if target_date == date.today() and season == today_season:
        asyncio.run(refresh_today_schedule())
        return
    asyncio.run(_fetch_target_scoreboard(target_date, season))


async def _fetch_target_scoreboard(target_date: date, season: str) -> None:
    async with NBAStatsScraper() as scraper:
        await scraper.fetch_scoreboard(season=season, game_date=target_date)
    summary = NBAStatsLoader().load_scoreboard_schedule(season=season, game_date=target_date)
    logger.info(
        "pregame_schedule_refreshed_for_target_date",
        season=season,
        game_date=target_date.isoformat(),
        files_loaded=summary.files_loaded,
        files_skipped=summary.files_skipped,
        games_upserted=summary.games_upserted,
    )


def _build_odds_tables_from_csv(target_date: date) -> dict[str, int]:
    path = _fallback_csv_path(target_date)
    if not path.exists():
        logger.warning("pregame_csv_fallback_missing", path=str(path))
        return {"games_count": _count_games_for_date(target_date), "markets_ingested": 0, "normalized_rows": 0, "csv_fallback_rows": 0}
    normalized_rows = 0
    inserted = 0
    games_count = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            games_count = _count_games(cur, target_date)
            _clear_odds_tables(cur, target_date)
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if not row.get("game_id") or not row.get("market"):
                        continue
                    cur.execute(
                        """
                        INSERT INTO odds (game_id, player_id, market, book, line, price, odds_timestamp)
                        VALUES (%(game_id)s, %(player_id)s, %(market)s, %(book)s, %(line)s, %(price)s, %(odds_timestamp)s)
                        """,
                        {
                            "game_id": row["game_id"],
                            "player_id": row.get("player_id") or "0",
                            "market": row["market"],
                            "book": row.get("book") or "csv_manual",
                            "line": float(row.get("line", "0")),
                            "price": int(row.get("price", "-110")),
                            "odds_timestamp": row.get("odds_timestamp") or datetime.now(UTC).isoformat(),
                        },
                    )
                    inserted += int(cur.rowcount or 0)
            grouped = _group_odds_rows(cur, target_date)
            for (game_id, player_id, market), values in grouped.items():
                _upsert_normalized_row(cur, game_id, player_id, market, values)
                normalized_rows += 1
    return {
        "games_count": games_count,
        "markets_ingested": inserted,
        "normalized_rows": normalized_rows,
        "csv_fallback_rows": inserted,
    }


def _fallback_csv_path(target_date: date) -> Path:
    explicit = os.getenv("PREGAME_ODDS_CSV")
    if explicit:
        return Path(explicit)
    return Path("data") / "odds" / f"manual_odds_{target_date.isoformat()}.csv"


def _build_odds_tables(target_date: date) -> dict[str, int]:
    normalized_rows = 0
    inserted = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            games_count = _count_games(cur, target_date)
            _clear_odds_tables(cur, target_date)
            inserted = _copy_raw_odds(cur, target_date)
            grouped = _group_odds_rows(cur, target_date)
            for (game_id, player_id, market), values in grouped.items():
                _upsert_normalized_row(cur, game_id, player_id, market, values)
                normalized_rows += 1
    return {
        "games_count": games_count,
        "markets_ingested": inserted,
        "normalized_rows": normalized_rows,
    }


def _count_games(cur: Any, target_date: date) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM games WHERE date = %(d)s", {"d": target_date})
    return int(cur.fetchone()["n"])


def _clear_odds_tables(cur: Any, target_date: date) -> None:
    params = {"d": target_date}
    cur.execute(
        "DELETE FROM odds WHERE game_id IN (SELECT game_id FROM games WHERE date = %(d)s)",
        params,
    )
    cur.execute(
        "DELETE FROM odds_normalized WHERE game_id IN (SELECT game_id FROM games WHERE date = %(d)s)",
        params,
    )


def _copy_raw_odds(cur: Any, target_date: date) -> int:
    cur.execute(
        """
        INSERT INTO odds (game_id, player_id, market, book, line, price, odds_timestamp)
        SELECT pl.game_id, pl.player_id, pl.market, pl.book, pl.line, pl.over_price, pl.snapshot_ts
        FROM prop_lines pl
        JOIN games g ON g.game_id = pl.game_id
        JOIN players p ON p.player_id::text = pl.player_id::text
        WHERE g.date = %(d)s
          AND p.team_id IN (g.home_team_id, g.away_team_id)
        """,
        {"d": target_date},
    )
    return int(cur.rowcount or 0)


def _group_odds_rows(cur: Any, target_date: date) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    cur.execute(
        """
        SELECT o.game_id, o.player_id, o.market, o.book, o.line, o.price, o.odds_timestamp
        FROM odds o
        JOIN games g ON g.game_id = o.game_id
        JOIN players p ON p.player_id::text = o.player_id::text
        WHERE g.date = %(d)s
          AND p.team_id IN (g.home_team_id, g.away_team_id)
        """,
        {"d": target_date},
    )
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        key = (str(row["game_id"]), str(row["player_id"]), str(row["market"]))
        grouped.setdefault(key, []).append(row)
    return grouped


def _upsert_normalized_row(
    cur: Any, game_id: str, player_id: str, market: str, values: list[dict[str, Any]]
) -> None:
    lines = [float(v["line"]) for v in values]
    prices = [int(v["price"]) for v in values]
    consensus_line = median(lines)
    consensus_price = sum(prices) / max(len(prices), 1)
    best_offer = max(values, key=lambda row: int(row["price"]))
    best_book = str(best_offer["book"])
    best_price = int(best_offer["price"])
    odds_asof = max(row["odds_timestamp"] for row in values)
    fair_prob = _american_to_prob(int(round(consensus_price)))
    cur.execute(
        """
        INSERT INTO odds_normalized (
            game_id, player_id, market, consensus_line, consensus_price, fair_prob, best_book, best_price, odds_asof, generated_at
        )
        VALUES (
            %(game_id)s, %(player_id)s, %(market)s, %(line)s, %(price)s, %(prob)s, %(best_book)s, %(best_price)s, %(odds_asof)s, NOW()
        )
        ON CONFLICT (game_id, player_id, market) DO UPDATE SET
            consensus_line = EXCLUDED.consensus_line,
            consensus_price = EXCLUDED.consensus_price,
            fair_prob = EXCLUDED.fair_prob,
            best_book = EXCLUDED.best_book,
            best_price = EXCLUDED.best_price,
            odds_asof = EXCLUDED.odds_asof,
            generated_at = EXCLUDED.generated_at
        """,
        {
            "game_id": game_id,
            "player_id": player_id,
            "market": market,
            "line": consensus_line,
            "price": consensus_price,
            "prob": fair_prob,
            "best_book": best_book,
            "best_price": best_price,
            "odds_asof": odds_asof,
        },
    )


def run_features(target_date: date, season: str) -> StepResult:
    """Compute deterministic game-level feature rows."""
    _ensure_pregame_tables()
    del season
    feature_count, names = _build_features_for_date(target_date)
    if _count_games_for_date(target_date) > 0 and feature_count == 0:
        logger.warning(
            "pregame_no_feature_rows_for_games",
            date=target_date.isoformat(),
            games_count=_count_games_for_date(target_date),
        )
    return StepResult(name="3_features", details={"feature_rows": feature_count, "feature_names": sorted(names)})


def _build_features_for_date(target_date: date) -> tuple[int, set[str]]:
    now = datetime.now(UTC)
    feature_count = 0
    names: set[str] = set()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT o.game_id, o.player_id
                FROM odds_normalized o
                JOIN games g ON g.game_id = o.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            player_rows = [(str(r["game_id"]), str(r["player_id"])) for r in cur.fetchall()]
            if not player_rows:
                return 0, set()
            cur.execute("DELETE FROM features WHERE game_id IN (SELECT game_id FROM games WHERE date = %(d)s)", {"d": target_date})
            for game_id, player_id in player_rows:
                generated = _feature_values_for_player(cur, game_id, player_id, target_date)
                for feature_name, value in generated.items():
                    cur.execute(
                        """
                        INSERT INTO features (game_id, player_id, feature_name, value, generated_at)
                        VALUES (%(g)s, %(p)s, %(n)s, %(v)s, %(t)s)
                        """,
                        {"g": game_id, "p": player_id, "n": feature_name, "v": value, "t": now},
                    )
                    feature_count += 1
                    names.add(feature_name)
    return feature_count, names


def _feature_values_for_player(cur: Any, game_id: str, player_id: str, target_date: date) -> dict[str, float]:
    home_team_id, away_team_id = _game_teams(cur, game_id)
    player_team_id = _player_team_id(cur, player_id)
    opponent_team_id = away_team_id if player_team_id == home_team_id else home_team_id
    if player_team_id not in {home_team_id, away_team_id}:
        player_team_id = home_team_id
        opponent_team_id = away_team_id
    rolling_points, rolling_minutes, rolling_usage = _player_rolling_stats(cur, player_id, target_date, 10)
    injury_impact = _player_injury_impact(cur, player_id, player_team_id, target_date)
    opp_vs_position = _opponent_vs_position_allowed(cur, opponent_team_id, player_id, target_date)
    matchup_points = _player_matchup_history_points(cur, player_id, opponent_team_id, target_date)
    spread_abs = _spread_abs_for_game(cur, game_id)
    home_flag = 1.0 if player_team_id == home_team_id else 0.0
    return {
        "market_prob": _latest_market_prob(cur, game_id, player_id),
        "injury_count": float(_injury_count_for_game(cur, game_id, target_date)),
        "home_flag": home_flag,
        "team_rest_days": float(_team_rest_days(cur, player_team_id, target_date)),
        "opp_rest_days": float(_team_rest_days(cur, opponent_team_id, target_date)),
        "team_win_pct_l10": _team_win_pct(cur, player_team_id, target_date, lookback=10),
        "opp_win_pct_l10": _team_win_pct(cur, opponent_team_id, target_date, lookback=10),
        "spread_abs": spread_abs,
        "player_points_l10": rolling_points,
        "player_minutes_l10": rolling_minutes,
        "player_usage_l10": rolling_usage,
        "injury_on_off_delta": injury_impact,
        "opp_vs_position_pts_allowed": opp_vs_position,
        "matchup_points_avg": matchup_points,
    }


def _count_games_for_date(target_date: date) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM games WHERE date = %(d)s", {"d": target_date})
            return int(cur.fetchone()["n"])


def _game_teams(cur: Any, game_id: str) -> tuple[str, str]:
    cur.execute(
        "SELECT home_team_id, away_team_id FROM games WHERE game_id = %(g)s",
        {"g": game_id},
    )
    row = cur.fetchone()
    if row is None:
        return "", ""
    return str(row["home_team_id"]), str(row["away_team_id"])


def _player_team_id(cur: Any, player_id: str) -> str:
    cur.execute("SELECT team_id FROM players WHERE player_id::text = %(p)s::text", {"p": player_id})
    row = cur.fetchone()
    if row is None or row["team_id"] is None:
        return ""
    return str(row["team_id"])


def _player_rolling_stats(cur: Any, player_id: str, target_date: date, lookback: int) -> tuple[float, float, float]:
    cur.execute(
        """
        SELECT pgs.points, pgs.minutes, pgs.usage_rate
        FROM player_game_stats pgs
        JOIN games g ON g.game_id = pgs.game_id
        WHERE pgs.player_id::text = %(p)s::text
          AND g.date < %(d)s
        ORDER BY g.date DESC
        LIMIT %(n)s
        """,
        {"p": player_id, "d": target_date, "n": lookback},
    )
    rows = cur.fetchall()
    if not rows:
        return 0.0, 0.0, 0.0
    pts = [float(r["points"] or 0.0) for r in rows]
    mins = [float(r["minutes"] or 0.0) for r in rows]
    usg = [float(r["usage_rate"] or 0.0) for r in rows]
    return sum(pts) / len(pts), sum(mins) / len(mins), sum(usg) / len(usg)


def _player_injury_impact(cur: Any, player_id: str, team_id: str, target_date: date) -> float:
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM injury_status i
        JOIN players p ON p.player_id = i.player_id
        WHERE p.team_id::text = %(t)s::text
          AND i.status_date::date = %(d)s
          AND i.status IN ('out', 'doubtful', 'questionable')
        """,
        {"t": team_id, "d": target_date},
    )
    out_count = int(cur.fetchone()["n"])
    _, _, usage = _player_rolling_stats(cur, player_id, target_date, 5)
    return usage * (0.01 * out_count)


def _opponent_vs_position_allowed(cur: Any, opponent_team_id: str, player_id: str, target_date: date) -> float:
    cur.execute("SELECT position FROM players WHERE player_id::text = %(p)s::text", {"p": player_id})
    row = cur.fetchone()
    position = str(row["position"] or "").strip()
    if not position:
        return 0.0
    cur.execute(
        """
        SELECT pgs.points
        FROM player_game_stats pgs
        JOIN games g ON g.game_id = pgs.game_id
        JOIN players p ON p.player_id = pgs.player_id
        WHERE g.date < %(d)s
          AND p.position = %(pos)s
          AND (
            (g.home_team_id::text = %(opp)s::text AND pgs.team_id::text <> %(opp)s::text) OR
            (g.away_team_id::text = %(opp)s::text AND pgs.team_id::text <> %(opp)s::text)
          )
        ORDER BY g.date DESC
        LIMIT 50
        """,
        {"d": target_date, "pos": position, "opp": opponent_team_id},
    )
    rows = cur.fetchall()
    if not rows:
        return 0.0
    vals = [float(r["points"] or 0.0) for r in rows]
    return sum(vals) / len(vals)


def _player_matchup_history_points(cur: Any, player_id: str, opponent_team_id: str, target_date: date) -> float:
    cur.execute(
        """
        SELECT pgs.points
        FROM player_game_stats pgs
        JOIN games g ON g.game_id = pgs.game_id
        WHERE pgs.player_id::text = %(p)s::text
          AND g.date < %(d)s
          AND (g.home_team_id::text = %(opp)s::text OR g.away_team_id::text = %(opp)s::text)
        ORDER BY g.date DESC
        LIMIT 8
        """,
        {"p": player_id, "d": target_date, "opp": opponent_team_id},
    )
    rows = cur.fetchall()
    if not rows:
        return 0.0
    vals = [float(r["points"] or 0.0) for r in rows]
    return sum(vals) / len(vals)


def _team_rest_days(cur: Any, team_id: str, target_date: date) -> int:
    if not team_id:
        return 0
    cur.execute(
        """
        SELECT MAX(date) AS last_game_date
        FROM games
        WHERE date < %(d)s
          AND (home_team_id = %(t)s OR away_team_id = %(t)s)
        """,
        {"d": target_date, "t": team_id},
    )
    row = cur.fetchone()
    if row is None or row["last_game_date"] is None:
        return 3
    return int((target_date - row["last_game_date"]).days)


def _team_win_pct(cur: Any, team_id: str, target_date: date, lookback: int) -> float:
    if not team_id:
        return 0.5
    cur.execute(
        """
        SELECT home_team_id, away_team_id, home_score, away_score
        FROM games
        WHERE date < %(d)s
          AND (home_team_id = %(t)s OR away_team_id = %(t)s)
          AND home_score IS NOT NULL
          AND away_score IS NOT NULL
        ORDER BY date DESC
        LIMIT %(n)s
        """,
        {"d": target_date, "t": team_id, "n": lookback},
    )
    rows = cur.fetchall()
    if not rows:
        return 0.5
    wins = 0
    for row in rows:
        home = str(row["home_team_id"]) == team_id
        if home and int(row["home_score"]) > int(row["away_score"]):
            wins += 1
        if (not home) and int(row["away_score"]) > int(row["home_score"]):
            wins += 1
    return float(wins) / float(len(rows))


def _spread_abs_for_game(cur: Any, game_id: str) -> float:
    cur.execute(
        """
        SELECT consensus_line
        FROM odds_normalized
        WHERE game_id = %(g)s
          AND market ILIKE '%%spread%%'
        ORDER BY generated_at DESC
        LIMIT 1
        """,
        {"g": game_id},
    )
    row = cur.fetchone()
    if row is None or row["consensus_line"] is None:
        return 0.0
    return abs(float(row["consensus_line"]))


def run_model(target_date: date, season: str) -> StepResult:
    """Run a deterministic naive model using feature values."""
    _ensure_pregame_tables()
    del season
    scored = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM model_scores
                WHERE game_id IN (SELECT game_id FROM games WHERE date = %(d)s)
                """,
                {"d": target_date},
            )
            cur.execute(
                """
                SELECT
                    f.game_id,
                    f.feature_name,
                    f.value,
                    o.player_id,
                    o.market,
                    o.consensus_line,
                    o.fair_prob,
                    o.best_book,
                    o.best_price,
                    o.odds_asof
                FROM features f
                JOIN odds_normalized o
                  ON o.game_id = f.game_id
                 AND o.player_id::text = f.player_id::text
                JOIN games g ON g.game_id = f.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            rows = cur.fetchall()
            grouped: dict[tuple[str, str, str], dict[str, float]] = {}
            consensus_line: dict[tuple[str, str, str], float] = {}
            market_fair_prob: dict[tuple[str, str, str], float] = {}
            market_source: dict[tuple[str, str, str], tuple[str, int, datetime | None]] = {}
            for row in rows:
                key = (str(row["game_id"]), str(row["player_id"]), str(row["market"]))
                grouped.setdefault(key, {})[str(row["feature_name"])] = float(row["value"])
                consensus_line[key] = float(row["consensus_line"])
                market_fair_prob[key] = float(row["fair_prob"])
                market_source[key] = (
                    str(row["best_book"]),
                    int(row["best_price"]),
                    row.get("odds_asof"),
                )

            if not grouped:
                with get_connection() as check_conn:
                    with check_conn.cursor() as check_cur:
                        check_cur.execute(
                            """
                            SELECT COUNT(*) AS n
                            FROM features
                            WHERE game_id IN (SELECT game_id FROM games WHERE date = %(d)s)
                            """,
                            {"d": target_date},
                        )
                        feature_count = int(check_cur.fetchone()["n"])
                if feature_count > 0:
                    raise RuntimeError("Features exist but model produced zero scores.")
                return StepResult(name="4_model", details={"scores": 0, "model_version": MODEL_VERSION})

            for (game_id, player_id, market), feature_map in grouped.items():
                key = (game_id, player_id, market)
                base = market_fair_prob.get(key, 0.5)
                injury_penalty = min(feature_map.get("injury_count", 0.0) * 0.005, 0.05)
                form_delta = (feature_map.get("team_win_pct_l10", 0.5) - feature_map.get("opp_win_pct_l10", 0.5)) * 0.06
                rest_delta = (feature_map.get("team_rest_days", 1.0) - feature_map.get("opp_rest_days", 1.0)) * 0.01
                spread_delta = min(feature_map.get("spread_abs", 0.0) * 0.002, 0.03)
                player_form = min(feature_map.get("player_points_l10", 0.0) / 40.0, 1.0) * 0.04
                usage_form = min(feature_map.get("player_usage_l10", 0.0) / 35.0, 1.0) * 0.03
                minutes_form = min(feature_map.get("player_minutes_l10", 0.0) / 40.0, 1.0) * 0.02
                injury_on_off = min(feature_map.get("injury_on_off_delta", 0.0), 0.04)
                opp_pos = min(feature_map.get("opp_vs_position_pts_allowed", 0.0) / 40.0, 1.0) * 0.03
                matchup_hist = min(feature_map.get("matchup_points_avg", 0.0) / 40.0, 1.0) * 0.03
                modeled = min(
                    max(
                        base
                        - injury_penalty
                        + form_delta
                        + rest_delta
                        + spread_delta
                        + player_form
                        + usage_form
                        + minutes_form
                        + injury_on_off
                        + opp_pos
                        + matchup_hist,
                        0.01,
                    ),
                    0.99,
                )
                cur.execute(
                    """
                    INSERT INTO model_scores (
                        game_id, player_id, market, model_prob, model_line, market_book, market_price, odds_asof, scored_at, model_version
                    )
                    VALUES (
                        %(game_id)s, %(player_id)s, %(market)s, %(model_prob)s, %(model_line)s, %(market_book)s, %(market_price)s, %(odds_asof)s, NOW(), %(model_version)s
                    )
                    """,
                    {
                        "game_id": game_id,
                        "player_id": player_id,
                        "market": market,
                        "model_prob": modeled,
                        "model_line": consensus_line[(game_id, player_id, market)],
                        "market_book": market_source[(game_id, player_id, market)][0],
                        "market_price": market_source[(game_id, player_id, market)][1],
                        "odds_asof": market_source[(game_id, player_id, market)][2],
                        "model_version": MODEL_VERSION,
                    },
                )
                scored += 1
    return StepResult(name="4_model", details={"scores": scored, "model_version": MODEL_VERSION})


def run_edge_selection(target_date: date, season: str) -> StepResult:
    """Compute edges and materialize active picks above threshold."""
    _ensure_pregame_tables()
    del season
    edges_total = 0
    picks_total = 0
    rejected_stale = 0
    rejected_threshold = 0
    rejected_exposure = 0
    top_edges: list[dict[str, Any]] = []
    now_utc = datetime.now(UTC)
    # For retroactive runs (past dates) measure staleness from midnight after game day
    # so odds captured on game day are never falsely rejected as stale.
    if target_date < now_utc.date():
        from datetime import timedelta
        next_day = target_date + timedelta(days=1)
        now_utc = datetime(next_day.year, next_day.month, next_day.day, tzinfo=UTC)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM edges
                WHERE game_id IN (SELECT game_id FROM games WHERE date = %(d)s)
                """,
                {"d": target_date},
            )
            cur.execute("DELETE FROM picks WHERE game_date = %(d)s", {"d": target_date})
            cur.execute(
                """
                SELECT ms.game_id, ms.market, ms.model_prob, ms.model_line, onr.fair_prob
                     , ms.player_id, ms.market_book, ms.market_price, ms.odds_asof
                FROM model_scores ms
                JOIN odds_normalized onr
                  ON onr.game_id = ms.game_id
                 AND onr.player_id = ms.player_id
                 AND onr.market = ms.market
                JOIN games g ON g.game_id = ms.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            rows = cur.fetchall()
            rows_sorted = sorted(
                [dict(row) for row in rows],
                key=lambda r: _edge_pct(float(r["model_prob"]), float(r["fair_prob"])),
                reverse=True,
            )
            player_pick_counts: dict[str, int] = {}
            game_pick_counts: dict[str, int] = {}
            selected_for_picks: list[dict[str, Any]] = []
            for row in rows_sorted:
                model_prob = float(row["model_prob"])
                market_prob = float(row["fair_prob"])
                edge = _edge_pct(model_prob, market_prob)
                side = "over" if edge >= 0 else "under"
                market = str(row["market"])
                player_id = str(row["player_id"])
                game_id = str(row["game_id"])
                decision = "keep"
                rejection_reason: str | None = None
                if _is_stale_odds(row.get("odds_asof"), now_utc):
                    decision = "reject"
                    rejection_reason = "stale_odds"
                    rejected_stale += 1
                threshold = _market_edge_threshold(market)
                if decision == "keep" and edge < threshold:
                    decision = "reject"
                    rejection_reason = "below_threshold"
                    rejected_threshold += 1
                if decision == "keep" and player_pick_counts.get(player_id, 0) >= MAX_PICKS_PER_PLAYER:
                    decision = "reject"
                    rejection_reason = "player_cap"
                    rejected_exposure += 1
                if decision == "keep" and game_pick_counts.get(game_id, 0) >= MAX_PICKS_PER_GAME:
                    decision = "reject"
                    rejection_reason = "game_cap"
                    rejected_exposure += 1

                cur.execute(
                    """
                    INSERT INTO edges (
                        game_id, player_id, market, side, model_prob, market_prob, edge_pct, market_book, market_price,
                        decision, rejection_reason, scored_at, model_version
                    )
                    VALUES (
                        %(game_id)s, %(player_id)s, %(market)s, %(side)s, %(model_prob)s, %(market_prob)s, %(edge)s,
                        %(market_book)s, %(market_price)s, %(decision)s, %(rejection_reason)s, NOW(), %(model_version)s
                    )
                    """,
                    {
                        "game_id": game_id,
                        "player_id": player_id,
                        "market": market,
                        "side": side,
                        "model_prob": model_prob,
                        "market_prob": market_prob,
                        "edge": edge,
                        "market_book": row["market_book"],
                        "market_price": row["market_price"],
                        "decision": decision,
                        "rejection_reason": rejection_reason,
                        "model_version": MODEL_VERSION,
                    },
                )
                edges_total += 1
                top_edges.append(
                    {
                        "game_id": game_id,
                        "player_id": player_id,
                        "market": market,
                        "side": side,
                        "edge_pct": edge,
                        "market_book": str(row["market_book"]),
                        "market_price": int(row["market_price"]),
                    }
                )
                if decision == "keep" and edge >= EDGE_THRESHOLD:
                    selected_for_picks.append(
                        {
                            "game_id": game_id,
                            "game_date": target_date,
                            "market": market,
                            "side": side,
                            "line": row["model_line"],
                            "edge": edge,
                            "model_version": MODEL_VERSION,
                            "player_id": player_id,
                            "book": row["market_book"],
                            "price": row["market_price"],
                        }
                    )
                    player_pick_counts[player_id] = player_pick_counts.get(player_id, 0) + 1
                    game_pick_counts[game_id] = game_pick_counts.get(game_id, 0) + 1
            selected_sorted = sorted(selected_for_picks, key=lambda r: float(r["edge"]), reverse=True)
            total_selected = len(selected_sorted)
            for idx, pick in enumerate(selected_sorted):
                confidence = _edge_confidence(float(pick["edge"]))
                grade, grade_reason = _grade_pick(float(pick["edge"]), idx, total_selected, str(pick["market"]))
                cur.execute(
                    """
                    INSERT INTO picks (
                        game_id, game_date, market, side, line, edge_pct, confidence, grade, grade_reason, status, model_version, created_at
                        , player_id, book, price
                    )
                    VALUES (
                        %(game_id)s, %(game_date)s, %(market)s, %(side)s, %(line)s, %(edge)s, %(confidence)s, %(grade)s, %(grade_reason)s,
                        'active', %(model_version)s, NOW(), %(player_id)s, %(book)s, %(price)s
                    )
                    """,
                    {
                        "game_id": pick["game_id"],
                        "game_date": pick["game_date"],
                        "market": pick["market"],
                        "side": pick["side"],
                        "line": pick["line"],
                        "edge": pick["edge"],
                        "confidence": confidence,
                        "grade": grade,
                        "grade_reason": grade_reason,
                        "model_version": pick["model_version"],
                        "player_id": pick["player_id"],
                        "book": pick["book"],
                        "price": pick["price"],
                    },
                )
                picks_total += 1
    top_sorted = sorted(top_edges, key=lambda x: float(x["edge_pct"]), reverse=True)[:10]
    if picks_total == 0:
        logger.warning("pregame_no_picks_passed_threshold", date=target_date.isoformat())
    return StepResult(
        name="5_edges",
        details={
            "edges": edges_total,
            "picks": picks_total,
            "top_10": top_sorted,
            "rejected_stale": rejected_stale,
            "rejected_threshold": rejected_threshold,
            "rejected_exposure": rejected_exposure,
        },
    )


def run_validation(target_date: date, season: str) -> StepResult:
    """Run strict validation gates and raise on failures."""
    _ensure_pregame_tables()
    del season
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM games WHERE date = %(d)s", {"d": target_date})
            games_count = int(cur.fetchone()["n"])
            cur.execute("SELECT COUNT(*) AS n FROM picks WHERE game_date = %(d)s", {"d": target_date})
            picks_count = int(cur.fetchone()["n"])
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM edges e
                JOIN games g ON g.game_id = e.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            edges_count = int(cur.fetchone()["n"])
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM odds_normalized o
                JOIN games g ON g.game_id = o.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            normalized_count = int(cur.fetchone()["n"])
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM features f
                JOIN games g ON g.game_id = f.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            features_count = int(cur.fetchone()["n"])
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM model_scores ms
                JOIN games g ON g.game_id = ms.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            model_scores_count = int(cur.fetchone()["n"])
            if games_count > 0 and edges_count == 0:
                if normalized_count == 0 and features_count == 0 and model_scores_count == 0:
                    logger.warning(
                        "pregame_validation_skipped_edges_check_no_market_data",
                        date=target_date.isoformat(),
                        games_count=games_count,
                    )
                else:
                    raise RuntimeError("Validation failed: games exist but edges table is empty.")
            if games_count > 0 and picks_count == 0:
                logger.warning("pregame_validation_no_picks", date=target_date.isoformat())

            cur.execute(
                """
                SELECT game_id, player_id, market, side, COUNT(*) AS n
                FROM picks
                WHERE game_date = %(d)s
                GROUP BY game_id, player_id, market, side
                HAVING COUNT(*) > 1
                """,
                {"d": target_date},
            )
            duplicates = cur.fetchall()
            if duplicates:
                raise RuntimeError("Validation failed: duplicate pick keys found.")

            cur.execute(
                """
                SELECT p.game_id
                FROM picks p
                LEFT JOIN games g ON g.game_id = p.game_id
                WHERE p.game_date = %(d)s
                  AND g.game_id IS NULL
                LIMIT 1
                """,
                {"d": target_date},
            )
            missing_game = cur.fetchone()
            if missing_game is not None:
                raise RuntimeError("Validation failed: picks contains unknown game_id.")

            cur.execute(
                """
                SELECT p.pick_id
                FROM picks p
                JOIN games g ON g.game_id = p.game_id
                JOIN players pl ON pl.player_id::text = p.player_id::text
                WHERE p.game_date = %(d)s
                  AND pl.team_id NOT IN (g.home_team_id, g.away_team_id)
                LIMIT 1
                """,
                {"d": target_date},
            )
            wrong_game_player = cur.fetchone()
            if wrong_game_player is not None:
                raise RuntimeError("Validation failed: pick player is not on either game team.")

            cur.execute(
                """
                SELECT onr.game_id
                FROM odds_normalized onr
                JOIN games g ON g.game_id = onr.game_id
                JOIN players pl ON pl.player_id::text = onr.player_id::text
                WHERE g.date = %(d)s
                  AND pl.team_id NOT IN (g.home_team_id, g.away_team_id)
                LIMIT 1
                """,
                {"d": target_date},
            )
            wrong_game_odds = cur.fetchone()
            if wrong_game_odds is not None:
                raise RuntimeError("Validation failed: odds row player is not on either game team.")

            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM edges e
                JOIN games g ON g.game_id = e.game_id
                WHERE g.date = %(d)s
                  AND (e.edge_pct < %(low)s OR e.edge_pct > %(high)s)
                """,
                {"d": target_date, "low": -EDGE_SANITY_BOUND, "high": EDGE_SANITY_BOUND},
            )
            out_of_range = int(cur.fetchone()["n"])
            if out_of_range > 0:
                raise RuntimeError("Validation failed: edge_pct outside sane bounds.")
    return StepResult(name="6_validate", details={"passed": True, "picks_count": picks_count})


def run_summary(target_date: date, season: str) -> StepResult:
    """Build and log summary values for the run."""
    _ensure_pregame_tables()
    del season
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM games WHERE date = %(d)s", {"d": target_date})
            games = int(cur.fetchone()["n"])
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM odds_normalized onr
                JOIN games g ON g.game_id = onr.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            markets = int(cur.fetchone()["n"])
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM edges e
                JOIN games g ON g.game_id = e.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            edges = int(cur.fetchone()["n"])
            cur.execute("SELECT COUNT(*) AS n FROM picks WHERE game_date = %(d)s", {"d": target_date})
            picks = int(cur.fetchone()["n"])
    return StepResult(
        name="7_summary",
        details={"date": target_date.isoformat(), "games": games, "markets": markets, "edges": edges, "picks": picks},
    )


def _injury_count_for_game(cur: Any, game_id: str, target_date: date) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM injury_status i
        JOIN players p ON p.player_id = i.player_id
        JOIN games g ON g.game_id = %(game_id)s
        WHERE i.status_date::date = %(d)s
          AND p.team_id IN (g.home_team_id, g.away_team_id)
          AND i.status IN ('out', 'doubtful', 'questionable')
        """,
        {"game_id": game_id, "d": target_date},
    )
    return int(cur.fetchone()["n"])


def _latest_market_prob(cur: Any, game_id: str, player_id: str) -> float:
    cur.execute(
        """
        SELECT fair_prob
        FROM odds_normalized
        WHERE game_id = %(game_id)s
          AND player_id::text = %(player_id)s::text
        ORDER BY generated_at DESC
        LIMIT 1
        """,
        {"game_id": game_id, "player_id": player_id},
    )
    row = cur.fetchone()
    if row is None:
        return 0.5
    return float(row["fair_prob"])


def _american_to_prob(odds_price: int) -> float:
    if odds_price == 0:
        return 0.5
    if odds_price > 0:
        return 100.0 / (float(odds_price) + 100.0)
    absolute = abs(float(odds_price))
    return absolute / (absolute + 100.0)


def _edge_confidence(edge_pct: float) -> str:
    if edge_pct >= 0.07:
        return "high"
    if edge_pct >= 0.04:
        return "medium"
    return "low"


def _edge_pct(model_prob: float, market_prob: float) -> float:
    raw = model_prob - market_prob
    return max(-EDGE_SANITY_BOUND, min(EDGE_SANITY_BOUND, raw))


def _market_edge_threshold(market: str) -> float:
    return MARKET_EDGE_THRESHOLDS.get(market, 0.07)


def _is_stale_odds(odds_asof: datetime | None, now_utc: datetime) -> bool:
    if odds_asof is None:
        return True
    if not isinstance(odds_asof, datetime):
        return True
    if odds_asof.tzinfo is None:
        return True
    age_minutes = (now_utc - odds_asof).total_seconds() / 60.0
    return age_minutes > STALE_ODDS_MINUTES


def _grade_pick(edge: float, rank_idx: int, total: int, market: str) -> tuple[str, str]:
    if total <= 0:
        return "C-", "No ranked context available."
    percentile = 1.0 - (rank_idx / max(total - 1, 1))
    grade = "C-"
    if edge >= 0.30 and percentile >= 0.90:
        grade = "A+"
    elif edge >= 0.22 and percentile >= 0.80:
        grade = "A"
    elif edge >= 0.17 and percentile >= 0.70:
        grade = "A-"
    elif edge >= 0.13 and percentile >= 0.60:
        grade = "B+"
    elif edge >= 0.10 and percentile >= 0.50:
        grade = "B"
    elif edge >= 0.08 and percentile >= 0.40:
        grade = "B-"
    elif edge >= 0.06:
        grade = "C+"
    elif edge >= 0.05:
        grade = "C"
    reason = (
        f"Edge {edge * 100:.1f}% in top {percentile * 100:.0f}% of accepted picks for market {market}."
    )
    return grade, reason


def _ensure_pregame_tables() -> None:
    """Create required pregame pipeline tables if they do not exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS odds (
                    id BIGSERIAL PRIMARY KEY,
                    game_id TEXT NOT NULL REFERENCES games(game_id),
                    player_id TEXT,
                    market TEXT NOT NULL,
                    book TEXT NOT NULL,
                    line NUMERIC NOT NULL,
                    price INT NOT NULL,
                    odds_timestamp TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS odds_normalized (
                    game_id TEXT NOT NULL REFERENCES games(game_id),
                    player_id TEXT,
                    market TEXT NOT NULL,
                    consensus_line NUMERIC NOT NULL,
                    consensus_price NUMERIC NOT NULL,
                    fair_prob NUMERIC NOT NULL,
                    best_book TEXT,
                    best_price INT,
                    odds_asof TIMESTAMPTZ,
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (game_id, player_id, market)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS features (
                    game_id TEXT NOT NULL REFERENCES games(game_id),
                    player_id TEXT,
                    feature_name TEXT NOT NULL,
                    value NUMERIC NOT NULL,
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (game_id, player_id, feature_name, generated_at)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS model_scores (
                    game_id TEXT NOT NULL REFERENCES games(game_id),
                    player_id TEXT,
                    market TEXT NOT NULL,
                    model_prob NUMERIC NOT NULL,
                    model_line NUMERIC,
                    market_book TEXT,
                    market_price INT,
                    odds_asof TIMESTAMPTZ,
                    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    model_version TEXT NOT NULL,
                    PRIMARY KEY (game_id, player_id, market, scored_at)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS edges (
                    game_id TEXT NOT NULL REFERENCES games(game_id),
                    player_id TEXT,
                    market TEXT NOT NULL,
                    side TEXT NOT NULL,
                    model_prob NUMERIC NOT NULL,
                    market_prob NUMERIC NOT NULL,
                    edge_pct NUMERIC NOT NULL,
                    market_book TEXT,
                    market_price INT,
                    decision TEXT,
                    rejection_reason TEXT,
                    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    model_version TEXT NOT NULL,
                    PRIMARY KEY (game_id, player_id, market, side, scored_at)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS picks (
                    pick_id BIGSERIAL PRIMARY KEY,
                    game_id TEXT NOT NULL REFERENCES games(game_id),
                    player_id TEXT,
                    game_date DATE NOT NULL,
                    market TEXT NOT NULL,
                    side TEXT NOT NULL,
                    line NUMERIC,
                    book TEXT,
                    price INT,
                    edge_pct NUMERIC NOT NULL,
                    confidence TEXT NOT NULL,
                    grade TEXT,
                    grade_reason TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    model_version TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("ALTER TABLE odds ADD COLUMN IF NOT EXISTS player_id TEXT")
            cur.execute("ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS player_id TEXT")
            cur.execute("ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS best_book TEXT")
            cur.execute("ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS best_price INT")
            cur.execute("ALTER TABLE odds_normalized ADD COLUMN IF NOT EXISTS odds_asof TIMESTAMPTZ")
            cur.execute("ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS player_id TEXT")
            cur.execute("ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS market_book TEXT")
            cur.execute("ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS market_price INT")
            cur.execute("ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS odds_asof TIMESTAMPTZ")
            cur.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS player_id TEXT")
            cur.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS market_book TEXT")
            cur.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS market_price INT")
            cur.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS decision TEXT")
            cur.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS rejection_reason TEXT")
            cur.execute("ALTER TABLE picks ADD COLUMN IF NOT EXISTS player_id TEXT")
            cur.execute("ALTER TABLE picks ADD COLUMN IF NOT EXISTS book TEXT")
            cur.execute("ALTER TABLE picks ADD COLUMN IF NOT EXISTS price INT")
            cur.execute("ALTER TABLE picks ADD COLUMN IF NOT EXISTS grade TEXT")
            cur.execute("ALTER TABLE picks ADD COLUMN IF NOT EXISTS grade_reason TEXT")
            cur.execute("ALTER TABLE features ADD COLUMN IF NOT EXISTS player_id TEXT")
            cur.execute("ALTER TABLE odds_normalized DROP CONSTRAINT IF EXISTS odds_normalized_pkey")
            cur.execute("ALTER TABLE model_scores DROP CONSTRAINT IF EXISTS model_scores_pkey")
            cur.execute("ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_pkey")
            cur.execute("ALTER TABLE features DROP CONSTRAINT IF EXISTS features_pkey")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_picks_date_status ON picks(game_date, status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_game_market ON edges(game_id, market)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_odds_game_market_book ON odds(game_id, market, book)")
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_odds_normalized_game_player_market ON odds_normalized(game_id, player_id, market)"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_scores_game_player_market_scored ON model_scores(game_id, player_id, market, scored_at)"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_edges_game_player_market_side_scored ON edges(game_id, player_id, market, side, scored_at)"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_features_game_player_feature_generated ON features(game_id, player_id, feature_name, generated_at)"
            )
