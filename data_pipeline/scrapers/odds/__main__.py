"""CLI entrypoint for odds scrapers and loader integration."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from contextlib import AsyncExitStack
from datetime import date, datetime, timezone
import sys
from typing import Any
from zoneinfo import ZoneInfo

from data_pipeline.config import Settings, get_settings
from data_pipeline.db import get_connection
from data_pipeline.loaders.moneyline_odds import MoneylineOddsLoader
from data_pipeline.loaders.nba_stats import NBAStatsLoader
from data_pipeline.loaders.odds import OddsLoader
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.nba_stats import NBAStatsScraper
from data_pipeline.scrapers.odds.base import (
    CanonicalMarket,
    OddsEvent,
    PropOffer,
    canonical_market_names,
    normalize_market_name,
    normalize_team_abbr,
)
from data_pipeline.scrapers.odds.betmgm import BetMGMScraper
from data_pipeline.scrapers.odds.draftkings import DraftKingsScraper
from data_pipeline.scrapers.odds.fanduel import FanDuelScraper

logger = get_logger(__name__)

ScraperType = DraftKingsScraper | FanDuelScraper | BetMGMScraper

# Scheduled-pairs type: each element is a frozenset of two team abbreviations.
ScheduledPairs = frozenset[frozenset[str]]


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the odds scraper CLI parser."""
    parser = argparse.ArgumentParser(description="Odds scraper")
    parser.add_argument("--book", choices=["draftkings", "fanduel", "betmgm", "all"], required=True)
    parser.add_argument("--markets", default="all")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--closing-lines", action="store_true")
    parser.add_argument(
        "--moneyline-only",
        action="store_true",
        help=(
            "Scrape only NBA team moneyline odds — no player props. "
            "Requires --season and --date to gate on the scheduled NBA slate. "
            "Useful for debugging team_moneyline_odds inserts."
        ),
    )
    parser.add_argument(
        "--season",
        help="NBA season label, e.g. 2025-26. Required with --moneyline-only.",
    )
    parser.add_argument(
        "--date",
        help="Target date YYYY-MM-DD. Required with --moneyline-only.",
    )
    return parser


def main() -> int:
    """Run the odds scraper CLI."""
    parser = build_argument_parser()
    args = parser.parse_args()
    if args.moneyline_only:
        if not args.season or not args.date:
            parser.error("--moneyline-only requires --season and --date.")
        target_date = date.fromisoformat(args.date)
        asyncio.run(run_moneyline_only(args.book, target_date=target_date, season=args.season))
        return 0
    selected_modes = sum(bool(flag) for flag in (args.once, args.watch, args.closing_lines))
    if selected_modes != 1:
        parser.error("Choose exactly one of --once, --watch, --closing-lines, or --moneyline-only.")
    markets = parse_markets_argument(args.markets)
    if args.once:
        asyncio.run(run_once(args.book, markets))
        return 0
    if args.watch:
        asyncio.run(run_watch(args.book, markets, closing_only=False))
        return 0
    asyncio.run(run_watch(args.book, markets, closing_only=True))
    return 0


async def run_moneyline_only(book: str, *, target_date: date, season: str) -> int:
    """Scrape NBA team moneyline odds only for the scheduled games on *target_date*.

    Loads the scheduled NBA slate from the DB, then restricts each sportsbook's
    event list to games that appear on that slate.  Non-NBA or unscheduled events
    (e.g. Turkish basketball) are filtered out before any fixture-view HTTP calls
    are made, so they never produce log noise or wasted requests.

    Args:
        book: Sportsbook key or ``"all"``.
        target_date: Calendar date of the NBA games to target.
        season: NBA season label, e.g. ``"2025-26"``.

    Returns:
        Total ``team_moneyline_odds`` rows inserted across all scrapers.
    """
    logger.info(
        "moneyline_only_starting",
        book=book,
        target_date=target_date.isoformat(),
        season=season,
    )
    # Refresh the schedule so the DB has today's games before we load pairs.
    await refresh_today_schedule()

    scheduled_pairs = _load_nba_scheduled_pairs(target_date, season)
    if not scheduled_pairs:
        logger.warning(
            "moneyline_only_no_scheduled_games",
            target_date=target_date.isoformat(),
            season=season,
            message="No NBA games found in DB for this date. Run schedule refresh first.",
        )
    else:
        matchups = [" vs ".join(sorted(pair)) for pair in scheduled_pairs]
        logger.info(
            "moneyline_only_scheduled_games",
            target_date=target_date.isoformat(),
            schedule_games_loaded=len(scheduled_pairs),
            scheduled_matchups=matchups,
        )

    moneyline_loader = MoneylineOddsLoader()
    total_inserted = 0
    async with AsyncExitStack() as stack:
        scrapers: list[ScraperType] = []
        for scraper in _build_scrapers(book):
            scrapers.append(await stack.enter_async_context(scraper))
        for scraper in scrapers:
            upserted = await scrape_moneyline_cycle(
                scraper=scraper,
                loader=moneyline_loader,
                scheduled_pairs=scheduled_pairs,
            )
            total_inserted += upserted
    logger.info(
        "moneyline_only_run_complete",
        book=book,
        target_date=target_date.isoformat(),
        moneyline_rows_inserted=total_inserted,
    )
    return total_inserted


async def run_once(book: str, markets: list[CanonicalMarket]) -> int:
    """Run one-shot scrapes for the selected books and markets.

    After collecting player-prop odds the function also runs a moneyline cycle
    for any scraper that implements ``fetch_all_moneyline_odds``.  The two passes
    share the same scraper session so only one HTTP client lifecycle is created.
    Moneyline odds are stored in ``team_moneyline_odds`` — they never touch
    the player-prop pipeline.
    """
    await refresh_today_schedule()
    loader = OddsLoader()
    moneyline_loader = MoneylineOddsLoader()
    inserted = 0
    async with AsyncExitStack() as stack:
        scrapers: list[ScraperType] = []
        for scraper in _build_scrapers(book):
            scrapers.append(await stack.enter_async_context(scraper))
        # Phase 1: player props (existing behaviour, unchanged).
        for scraper in scrapers:
            inserted += await scrape_book_cycle(
                scraper=scraper,
                markets=markets,
                loader=loader,
                capture_closing_lines=False,
                closing_only=False,
            )
        # Phase 2: team moneyline odds — no schedule filter here since run_once
        # is called from the daily pipeline which has already refreshed the schedule.
        for scraper in scrapers:
            inserted += await scrape_moneyline_cycle(scraper=scraper, loader=moneyline_loader)
    return inserted


async def run_watch(
    book: str,
    markets: list[CanonicalMarket],
    *,
    closing_only: bool,
) -> None:
    """Run the watch loop or dedicated closing-line loop."""
    settings = get_settings()
    await refresh_today_schedule()
    loader = OddsLoader()
    async with AsyncExitStack() as stack:
        scrapers: list[ScraperType] = []
        for scraper in _build_scrapers(book):
            scrapers.append(await stack.enter_async_context(scraper))
        while True:
            upcoming_events = await scrape_watch_cycle(
                scrapers=scrapers,
                markets=markets,
                loader=loader,
                closing_only=closing_only,
            )
            next_sleep = compute_next_sleep_seconds(upcoming_events, settings, closing_only)
            if next_sleep is None:
                logger.info("odds_watch_complete", closing_only=closing_only)
                return
            logger.info(
                "odds_watch_sleeping",
                closing_only=closing_only,
                sleep_seconds=next_sleep,
                upcoming_events=len(upcoming_events),
            )
            await asyncio.sleep(next_sleep)


async def scrape_watch_cycle(
    *,
    scrapers: Sequence[ScraperType],
    markets: list[CanonicalMarket],
    loader: OddsLoader,
    closing_only: bool,
) -> list[OddsEvent]:
    """Run one watch-loop scrape cycle across the selected books."""
    await refresh_today_schedule()
    upcoming_events: list[OddsEvent] = []
    for scraper in scrapers:
        events = await scraper.list_events()
        upcoming_events.extend(filter_future_events(events))
        await scrape_book_cycle(
            scraper=scraper,
            markets=markets,
            loader=loader,
            capture_closing_lines=True,
            closing_only=closing_only,
            preloaded_events=events,
        )
    return upcoming_events


async def scrape_book_cycle(
    *,
    scraper: ScraperType,
    markets: list[CanonicalMarket],
    loader: OddsLoader,
    capture_closing_lines: bool,
    closing_only: bool,
    preloaded_events: list[OddsEvent] | None = None,
) -> int:
    """Scrape and load one polling cycle for a single bookmaker."""
    events = preloaded_events if preloaded_events is not None else await scraper.list_events()
    active_events = select_active_events(events, closing_only)
    logger.info(
        "odds_events_listed",
        book=scraper.bookmaker_key,
        event_count=len(events),
        active_event_count=len(active_events),
        closing_only=closing_only,
    )
    if not active_events:
        return 0
    inserted = 0
    for market in markets:
        try:
            offers = await fetch_market_offers(scraper, active_events, market)
            summary = loader.load_snapshot(
                offers,
                bookmaker_key=scraper.bookmaker_key,
                market=market,
                capture_closing_lines=capture_closing_lines,
            )
            logger.info(
                "odds_market_loaded",
                book=scraper.bookmaker_key,
                market=market,
                offer_count=len(offers),
                inserted=summary.inserted,
                deduped=summary.deduped,
                unresolved_players=summary.unresolved_players,
                unresolved_events=summary.unresolved_events,
                closing_lines_upserted=summary.closing_lines_upserted,
            )
            inserted += summary.inserted
        except Exception as exc:
            loader.load_snapshot(
                [],
                bookmaker_key=scraper.bookmaker_key,
                market=market,
                error_message=str(exc),
                capture_closing_lines=capture_closing_lines,
            )
            logger.exception(
                "odds_market_scrape_failed",
                book=scraper.bookmaker_key,
                market=market,
                error_message=str(exc),
            )
    return inserted


async def scrape_moneyline_cycle(
    *,
    scraper: ScraperType,
    loader: MoneylineOddsLoader,
    scheduled_pairs: ScheduledPairs | None = None,
) -> int:
    """Collect NBA team moneyline odds for one sportsbook and persist them.

    When *scheduled_pairs* is provided the bookmaker event list is filtered to
    only events whose normalised team abbreviations match a scheduled game.
    Non-NBA or unscheduled events (e.g. Turkish basketball) are skipped before
    any per-fixture HTTP calls are made.

    Skips silently if the scraper does not implement ``fetch_all_moneyline_odds``
    (e.g. FanDuel).  Failures are caught and logged as warnings so the
    player-prop pipeline is never blocked by a moneyline scrape error.

    Returns:
        Number of rows upserted into ``team_moneyline_odds``.
    """
    logger.info("moneyline_cycle_starting", book=scraper.bookmaker_key)
    if not hasattr(scraper, "fetch_all_moneyline_odds"):
        logger.info("moneyline_cycle_skipped_no_impl", book=scraper.bookmaker_key)
        return 0
    try:
        events = await scraper.list_events()
        active_events = select_active_events(events, closing_only=False)
        logger.info(
            "moneyline_events_listed",
            book=scraper.bookmaker_key,
            total_events=len(events),
            active_events=len(active_events),
        )
        if not active_events:
            logger.info("moneyline_no_active_events", book=scraper.bookmaker_key)
            return 0

        # Gate on the scheduled NBA slate when pairs are provided.
        if scheduled_pairs is not None:
            nba_events, skipped_events = _filter_events_to_nba_schedule(
                active_events, scheduled_pairs
            )
            for ev in skipped_events:
                logger.info(
                    "moneyline_event_skipped_not_scheduled",
                    book=scraper.bookmaker_key,
                    bookmaker_event_id=ev.bookmaker_event_id,
                    home_team=ev.home_team_name,
                    away_team=ev.away_team_name,
                    reason="not in scheduled NBA slate",
                )
            logger.info(
                "moneyline_events_matched_to_schedule",
                book=scraper.bookmaker_key,
                bookmaker_events_fetched=len(active_events),
                bookmaker_events_matched=len(nba_events),
                bookmaker_events_skipped=len(skipped_events),
            )
            if not nba_events:
                logger.warning(
                    "moneyline_no_scheduled_nba_moneylines",
                    book=scraper.bookmaker_key,
                    message="No bookmaker events matched scheduled NBA games. "
                    "Check that the schedule is loaded for the target date.",
                )
                return 0
            active_events = nba_events

        offers = await scraper.fetch_all_moneyline_odds(active_events)
        logger.info(
            "moneyline_offers_fetched",
            book=scraper.bookmaker_key,
            moneyline_offers_parsed=len(offers),
        )
        if not offers:
            logger.warning(
                "moneyline_no_offers_parsed",
                book=scraper.bookmaker_key,
                message="Bookmaker events were found but zero moneyline offers parsed. "
                "Check subcategory IDs or market name matching.",
            )
            return 0
        summary = loader.load_snapshot(offers)
        logger.info(
            "moneyline_cycle_complete",
            book=scraper.bookmaker_key,
            total_offers=summary.total_offers,
            upserted=summary.upserted,
            unresolved_events=summary.unresolved_events,
            games_with_both_sides=summary.games_both_sides,
            games_missing_one_side=summary.games_missing_side,
        )
        return summary.upserted
    except Exception as exc:
        logger.warning(
            "moneyline_cycle_failed",
            book=scraper.bookmaker_key,
            error=str(exc),
        )
        return 0


async def fetch_market_offers(
    scraper: ScraperType,
    events: list[OddsEvent],
    market: CanonicalMarket,
) -> list[PropOffer]:
    """Fetch all offers for one book/market across the selected events."""
    offers = []
    for event in events:
        event_offers = await scraper.fetch_player_props(event, market)
        offers.extend(event_offers)
    return offers


def parse_markets_argument(raw_markets: str) -> list[CanonicalMarket]:
    """Parse CLI market tokens into canonical market identifiers."""
    tokens = [token.strip() for token in raw_markets.split(",") if token.strip()]
    if not tokens:
        raise ValueError("At least one market must be provided.")
    if any(token.lower() == "all" for token in tokens):
        return list(canonical_market_names())
    return [normalize_market_name(token) for token in tokens]


def select_active_events(events: Sequence[OddsEvent], closing_only: bool) -> list[OddsEvent]:
    """Filter listed events to the active set for the current polling mode."""
    now = datetime.now(timezone.utc)
    if closing_only:
        return [event for event in events if 0 <= (event.commence_time - now).total_seconds() <= 300]
    return [event for event in events if event.commence_time > now]


def filter_future_events(events: Sequence[OddsEvent]) -> list[OddsEvent]:
    """Return future events only."""
    now = datetime.now(timezone.utc)
    return [event for event in events if event.commence_time > now]


def compute_next_sleep_seconds(
    events: Sequence[OddsEvent],
    settings: Settings,
    closing_only: bool,
) -> int | None:
    """Compute the next poll interval from the nearest future tipoff."""
    future_events = filter_future_events(events)
    if not future_events:
        return None
    next_tipoff = min(event.commence_time for event in future_events)
    seconds_until_tip = (next_tipoff - datetime.now(timezone.utc)).total_seconds()
    if closing_only:
        if seconds_until_tip > settings.odds_poll_closing_window_minutes * 60:
            wait_seconds = int(seconds_until_tip - (settings.odds_poll_closing_window_minutes * 60))
            return max(1, min(wait_seconds, settings.odds_poll_interval_near_seconds))
        return settings.odds_poll_interval_closing_seconds
    if seconds_until_tip > settings.odds_poll_start_window_hours * 3600:
        wait_seconds = int(seconds_until_tip - (settings.odds_poll_start_window_hours * 3600))
        return max(1, wait_seconds)
    if seconds_until_tip <= settings.odds_poll_closing_window_minutes * 60:
        return settings.odds_poll_interval_closing_seconds
    if seconds_until_tip <= settings.odds_poll_near_window_minutes * 60:
        return settings.odds_poll_interval_near_seconds
    return settings.odds_poll_interval_far_minutes * 60


def _load_nba_scheduled_pairs(target_date: date, season: str) -> ScheduledPairs:
    """Return scheduled NBA matchups for *target_date* as a frozenset of team-abbr pairs.

    Each element is ``frozenset({home_abbr, away_abbr})`` using canonical three-letter
    abbreviations from the ``teams`` table.  Returns an empty frozenset if the DB is
    unavailable or no games are scheduled.
    """
    query = """
        SELECT home_t.abbreviation AS home_team, away_t.abbreviation AS away_team
        FROM games g
        JOIN teams home_t ON home_t.team_id = g.home_team_id
        JOIN teams away_t ON away_t.team_id = g.away_team_id
        WHERE g.date = %(target_date)s
          AND g.season = %(season)s
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, {"target_date": target_date.isoformat(), "season": season})
                rows = cur.fetchall()
        pairs: ScheduledPairs = frozenset(
            frozenset({str(row["home_team"]), str(row["away_team"])}) for row in rows
        )
        return pairs
    except Exception as exc:
        logger.warning(
            "moneyline_schedule_load_failed",
            target_date=target_date.isoformat(),
            season=season,
            error=str(exc),
        )
        return frozenset()


def _filter_events_to_nba_schedule(
    events: list[OddsEvent],
    scheduled_pairs: ScheduledPairs,
) -> tuple[list[OddsEvent], list[OddsEvent]]:
    """Split bookmaker events into (matched, skipped) against scheduled NBA pairs.

    An event matches when both its home and away team names normalise to NBA
    abbreviations that appear together in *scheduled_pairs*.  Events whose team
    names can't be normalised (e.g. "Petkim spor Aliaga") always go to *skipped*.

    Args:
        events: Active bookmaker events from ``scraper.list_events()``.
        scheduled_pairs: ``frozenset({home_abbr, away_abbr})`` for each NBA game.

    Returns:
        ``(matched, skipped)`` — two lists that together contain every input event.
    """
    matched: list[OddsEvent] = []
    skipped: list[OddsEvent] = []
    for event in events:
        try:
            home_abbr = normalize_team_abbr(event.home_team_name)
            away_abbr = normalize_team_abbr(event.away_team_name)
        except ValueError:
            skipped.append(event)
            continue
        pair = frozenset({home_abbr, away_abbr})
        if pair in scheduled_pairs:
            matched.append(event)
        else:
            skipped.append(event)
    return matched, skipped


def _build_scrapers(book: str) -> list[ScraperType]:
    if book == "draftkings":
        return [DraftKingsScraper()]
    if book == "fanduel":
        return [FanDuelScraper()]
    if book == "betmgm":
        return [BetMGMScraper()]
    return [DraftKingsScraper(), FanDuelScraper(), BetMGMScraper()]


async def refresh_today_schedule() -> None:
    """Refresh today's NBA schedule so future games can resolve before odds loads."""
    target_date = datetime.now(ZoneInfo("America/New_York")).date()
    season = infer_nba_season(target_date)
    async with NBAStatsScraper() as scraper:
        result = await scraper.fetch_scoreboard(season=season, game_date=target_date)
    summary = NBAStatsLoader().load_scoreboard_schedule(season=season, game_date=target_date)
    logger.info(
        "odds_schedule_refreshed",
        season=season,
        game_date=target_date.isoformat(),
        raw_file_path=str(result.raw_file_path),
        files_loaded=summary.files_loaded,
        files_skipped=summary.files_skipped,
        games_upserted=summary.games_upserted,
    )


def infer_nba_season(target_date: date) -> str:
    """Infer the NBA season label for a calendar date."""
    if target_date.month >= 7:
        start_year = target_date.year
    else:
        start_year = target_date.year - 1
    end_year = start_year + 1
    return f"{start_year}-{str(end_year)[-2:]}"


if __name__ == "__main__":
    sys.exit(main())
