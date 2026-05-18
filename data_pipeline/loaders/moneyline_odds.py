"""Loader for team moneyline odds snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from data_pipeline.db import get_connection
from data_pipeline.loaders.event_resolution import resolve_event_to_game_id
from data_pipeline.scrapers.odds.base import MoneylineOffer


@dataclass(frozen=True)
class MoneylineLoadSummary:
    """Summary metrics for moneyline load calls."""

    total_offers: int
    upserted: int
    unresolved_events: int
    games_both_sides: int
    games_missing_side: int


class MoneylineOddsLoader:
    """Load `MoneylineOffer` rows into `team_moneyline_odds`."""

    def load_snapshot(self, offers: list[MoneylineOffer]) -> MoneylineLoadSummary:
        upserted = 0
        unresolved_events = 0
        by_game: dict[str, set[str]] = {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                for offer in offers:
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
                    query = """
                        INSERT INTO team_moneyline_odds (
                            snapshot_ts, game_id, team_abbr, moneyline_odds, book, bookmaker_event_id, raw_response_path
                        ) VALUES (
                            %(snapshot_ts)s, %(game_id)s, %(team_abbr)s, %(moneyline_odds)s, %(book)s, %(bookmaker_event_id)s, %(raw_response_path)s
                        )
                        ON CONFLICT (snapshot_ts, game_id, team_abbr, book) DO UPDATE SET
                            moneyline_odds = EXCLUDED.moneyline_odds,
                            raw_response_path = EXCLUDED.raw_response_path
                    """
                    cur.execute(
                        query,
                        {
                            "snapshot_ts": offer.scraped_at.isoformat(),
                            "game_id": event.game_id,
                            "team_abbr": offer.team,
                            "moneyline_odds": offer.moneyline_odds,
                            "book": offer.bookmaker_key,
                            "bookmaker_event_id": offer.bookmaker_event_id,
                            "raw_response_path": offer.raw_response_path,
                        },
                    )
                    upserted += int(getattr(cur, "rowcount", 0) or 0)
                    by_game.setdefault(event.game_id, set()).add(offer.team)
        games_both_sides = sum(1 for teams in by_game.values() if len(teams) >= 2)
        games_missing_side = sum(1 for teams in by_game.values() if len(teams) < 2)
        return MoneylineLoadSummary(
            total_offers=len(offers),
            upserted=upserted,
            unresolved_events=unresolved_events,
            games_both_sides=games_both_sides,
            games_missing_side=games_missing_side,
        )
