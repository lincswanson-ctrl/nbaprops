"""Odds snapshot loader."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from data_pipeline.db import get_connection
from data_pipeline.loaders.event_resolution import resolve_event_to_game_id
from data_pipeline.loaders.name_resolution import resolve_player_name
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.odds.base import CanonicalMarket, PropOffer, normalize_team_abbr

logger = get_logger(__name__)


@dataclass(frozen=True)
class LoadSnapshotSummary:
    """Summary metrics from one load snapshot call."""

    inserted: int
    deduped: int
    unresolved_players: int
    unresolved_events: int
    closing_lines_upserted: int


class OddsLoader:
    """Load normalized odds offers into `prop_lines` / `closing_lines`."""

    def load_snapshot(
        self,
        offers: list[PropOffer],
        *,
        bookmaker_key: str,
        market: CanonicalMarket,
        error_message: str | None = None,
        capture_closing_lines: bool = False,
    ) -> LoadSnapshotSummary:
        inserted = 0
        deduped = 0
        unresolved_players = 0
        unresolved_events = 0
        closing_lines_upserted = 0
        started_at = datetime.now(timezone.utc)
        raw_paths: list[str] = []
        with get_connection() as conn:
            with conn.cursor() as cur:
                for offer in offers:
                    raw_paths.extend([offer.raw_response_path] if offer.raw_response_path else [])
                    if not _offer_has_safe_line_and_prices(offer):
                        logger.warning(
                            "odds_loader_skipped_invalid_offer",
                            bookmaker_key=offer.bookmaker_key,
                            event_id=offer.bookmaker_event_id,
                            player=offer.raw_player_name,
                            market=offer.market,
                            line=offer.line,
                            over_price=offer.over_price,
                            under_price=offer.under_price,
                            explicit_over_under=offer.explicit_over_under,
                        )
                        continue
                    event = resolve_event_to_game_id(
                        bookmaker_key=offer.bookmaker_key,
                        bookmaker_event_id=offer.bookmaker_event_id,
                        home_team_name=offer.home_team_name,
                        away_team_name=offer.away_team_name,
                        commence_time=offer.commence_time,
                    )
                    if event is None:
                        unresolved_events += 1
                        continue
                    team_hint = None
                    try:
                        team_hint = normalize_team_abbr(offer.away_team_name)
                    except ValueError:
                        team_hint = None
                    player_id = resolve_player_name(offer.raw_player_name, team_abbr=team_hint)
                    if player_id is None:
                        unresolved_players += 1
                        continue
                    if not _player_belongs_to_game_team(cur, game_id=event.game_id, player_id=player_id):
                        logger.warning(
                            "odds_loader_skipped_player_not_in_game",
                            bookmaker_key=offer.bookmaker_key,
                            event_id=offer.bookmaker_event_id,
                            game_id=event.game_id,
                            player_id=player_id,
                            player=offer.raw_player_name,
                        )
                        continue
                    row_count = _upsert_prop_line(cur, offer=offer, game_id=event.game_id, player_id=player_id)
                    inserted += row_count
                    deduped += 0 if row_count else 1
                    if capture_closing_lines:
                        closing_lines_upserted += _upsert_closing_line(
                            cur, offer=offer, game_id=event.game_id, player_id=player_id
                        )
                _insert_run_log(
                    cur,
                    bookmaker_key=bookmaker_key,
                    market=market,
                    started_at=started_at,
                    events_attempted=len(offers),
                    events_succeeded=max(len(offers) - unresolved_events, 0),
                    props_inserted=inserted,
                    error_message=error_message,
                    raw_response_paths=raw_paths,
                )
        return LoadSnapshotSummary(
            inserted=inserted,
            deduped=deduped,
            unresolved_players=unresolved_players,
            unresolved_events=unresolved_events,
            closing_lines_upserted=closing_lines_upserted,
        )


def _offer_has_safe_line_and_prices(offer: PropOffer) -> bool:
    """Return True when the offer cannot be an obvious synthetic or invalid O/U row."""

    if offer.line <= 0:
        return False
    if offer.over_price == offer.under_price and not offer.explicit_over_under:
        return False
    return True


def _player_belongs_to_game_team(cur: object, *, game_id: str, player_id: str) -> bool:
    """Return True when the player is rostered to either team in the resolved game."""

    query = """
        SELECT 1
        FROM games g
        JOIN players p
            ON p.player_id = %(player_id)s
        WHERE g.game_id = %(game_id)s
            AND p.team_id IN (g.home_team_id, g.away_team_id)
        LIMIT 1
    """
    cursor = cur
    cursor.execute(query, {"game_id": game_id, "player_id": player_id})
    fetchone = getattr(cursor, "fetchone", None)
    if fetchone is None:
        return False
    return fetchone() is not None


def _upsert_prop_line(cur: object, *, offer: PropOffer, game_id: str, player_id: str) -> int:
    query = """
        INSERT INTO prop_lines (
            snapshot_ts, game_id, player_id, market, line, over_price, under_price, book,
            bookmaker_event_id, bookmaker_key, raw_response_path
        ) VALUES (
            %(snapshot_ts)s, %(game_id)s, %(player_id)s, %(market)s, %(line)s, %(over_price)s, %(under_price)s, %(book)s,
            %(bookmaker_event_id)s, %(bookmaker_key)s, %(raw_response_path)s
        )
        ON CONFLICT (snapshot_ts, game_id, player_id, market, book) DO NOTHING
    """
    params = {
        "snapshot_ts": offer.scraped_at.astimezone(timezone.utc).isoformat(),
        "game_id": game_id,
        "player_id": player_id,
        "market": offer.market,
        "line": offer.line,
        "over_price": offer.over_price,
        "under_price": offer.under_price,
        "book": offer.bookmaker_key,
        "bookmaker_event_id": offer.bookmaker_event_id,
        "bookmaker_key": offer.bookmaker_key,
        "raw_response_path": offer.raw_response_path,
    }
    cursor = cur
    cursor.execute(query, params)
    return int(getattr(cursor, "rowcount", 0) or 0)


def _upsert_closing_line(cur: object, *, offer: PropOffer, game_id: str, player_id: str) -> int:
    minutes_before_tip = (offer.commence_time - offer.scraped_at).total_seconds() / 60.0
    if minutes_before_tip < 0:
        return 0
    query = """
        INSERT INTO closing_lines (
            game_id, player_id, market, line, over_price, under_price, book, captured_at, minutes_before_tip
        ) VALUES (
            %(game_id)s, %(player_id)s, %(market)s, %(line)s, %(over_price)s, %(under_price)s, %(book)s, %(captured_at)s, %(minutes_before_tip)s
        )
        ON CONFLICT (game_id, player_id, market, book) DO UPDATE SET
            line = EXCLUDED.line,
            over_price = EXCLUDED.over_price,
            under_price = EXCLUDED.under_price,
            captured_at = EXCLUDED.captured_at,
            minutes_before_tip = EXCLUDED.minutes_before_tip
        WHERE EXCLUDED.captured_at >= closing_lines.captured_at
    """
    cursor = cur
    cursor.execute(
        query,
        {
            "game_id": game_id,
            "player_id": player_id,
            "market": offer.market,
            "line": offer.line,
            "over_price": offer.over_price,
            "under_price": offer.under_price,
            "book": offer.bookmaker_key,
            "captured_at": offer.scraped_at.astimezone(timezone.utc).isoformat(),
            "minutes_before_tip": minutes_before_tip,
        },
    )
    return int(getattr(cursor, "rowcount", 0) or 0)


def _insert_run_log(
    cur: object,
    *,
    bookmaker_key: str,
    market: CanonicalMarket,
    started_at: datetime,
    events_attempted: int,
    events_succeeded: int,
    props_inserted: int,
    error_message: str | None,
    raw_response_paths: list[str],
) -> None:
    query = """
        INSERT INTO scraper_run_log (
            bookmaker_key, market, started_at, ended_at, events_attempted, events_succeeded,
            props_inserted, error_message, raw_response_paths
        ) VALUES (
            %(bookmaker_key)s, %(market)s, %(started_at)s, NOW(), %(events_attempted)s,
            %(events_succeeded)s, %(props_inserted)s, %(error_message)s, %(raw_response_paths)s
        )
    """
    cursor = cur
    cursor.execute(
        query,
        {
            "bookmaker_key": bookmaker_key,
            "market": market,
            "started_at": started_at.astimezone(timezone.utc).isoformat(),
            "events_attempted": events_attempted,
            "events_succeeded": events_succeeded,
            "props_inserted": props_inserted,
            "error_message": error_message,
            "raw_response_paths": raw_response_paths,
        },
    )
