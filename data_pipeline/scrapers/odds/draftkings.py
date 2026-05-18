"""DraftKings odds scraper."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from data_pipeline.config import get_settings
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.odds.base import (
    CanonicalMarket,
    MoneylineOffer,
    OddsEvent,
    PropOffer,
)

logger = get_logger(__name__)

_NBA_LEAGUE_ID = "42648"

# Event-specific endpoint subcategory IDs (discovered 2026-05-09 via Playwright capture).
# Used by _fetch_event_subcat_payload() — the correct endpoint for classic O/U props.
# Path: /controldata/event/eventSubcategory/v1/markets (not leagueSubcategory).
_EVENT_SUBCATEGORY_IDS: dict[CanonicalMarket, str] = {
    "player_points": "12488",              # Points O/U (marketType 12660)
    "player_rebounds": "12492",            # Rebounds O/U (marketType 12662)
    "player_assists": "12495",             # Assists O/U (marketType 12661)
    "player_threes": "12497",              # Three Pointers Made O/U (marketType 12663)
    "player_steals": "13508",              # Steals O/U (marketType 12664)
    "player_blocks": "13780",              # Blocks O/U (marketType 12666)
    "player_steals_blocks": "13781",       # Steals + Blocks O/U (marketType 12673)
    # Combo markets not confirmed posted yet — leave empty to skip gracefully:
    "player_points_rebounds_assists": "",
    "player_points_rebounds": "",
    "player_points_assists": "",
    "player_rebounds_assists": "",
    "player_turnovers": "",
}

# leagueSubcategory IDs for list_events() only (league-wide event discovery).
# All subcategories below are milestone/ladder — they correctly return 0 PropOffer
# because _dk_market_row_is_milestone() filters them. They exist solely so
# list_events() can union game events from multiple subcategory payloads.
_SUBCATEGORY_IDS: dict[CanonicalMarket, str] = {
    "player_points": "16477",              # Points Milestones (marketType 12189)
    "player_rebounds": "16479",            # Rebounds Milestones (marketType 12191)
    "player_assists": "16478",             # Assists Milestones (marketType 12190)
    "player_threes": "16480",              # 3PM Milestones (marketType 12206)
    "player_points_rebounds_assists": "16483",  # P+R+A Milestones (marketType 12194)
    "player_points_rebounds": "16482",     # P+R Milestones (marketType 12193)
    "player_points_assists": "16481",      # P+A Milestones (marketType 12192)
    "player_rebounds_assists": "16479",    # use rebounds milestone
    "player_steals": "16485",              # Steals Milestones (marketType 12196)
    "player_blocks": "16484",              # Blocks Milestones (marketType 12195)
    "player_steals_blocks": "16485",       # use steals milestone
    "player_turnovers": "16477",           # no dedicated sub found
}

# DraftKings marketType.id values for milestone-only ladders — never produce PropOffer.
_DK_MILESTONE_MARKET_TYPE_IDS: frozenset[str] = frozenset(
    {
        "12189",  # Points Milestones
        "12190",  # Assists Milestones
        "12191",  # Rebounds Milestones
        "12192",  # Points + Assists Milestones
        "12193",  # Points + Rebounds Milestones
        "12194",  # Points + Rebounds + Assists Milestones
        "12195",  # Blocks Milestones
        "12196",  # Steals Milestones
        "12206",  # Three Pointers Made Milestones
    }
)


class DraftKingsScraper:
    """Scrape DraftKings NBA odds endpoints."""

    bookmaker_key = "draftkings"

    def __init__(self) -> None:
        settings = get_settings()
        self._raw_root = settings.raw_storage_path / "odds" / self.bookmaker_key
        self._client: httpx.AsyncClient | None = None
        self._market_payload_cache: dict[str, dict[str, Any]] = {}

    def _player_points_subcategory_chain(self) -> tuple[str, ...]:
        """Return leagueSubcategory IDs to fetch for player points (classic O/U discovery)."""

        raw = get_settings().draftkings_player_points_subcategory_ids.strip()
        if raw:
            ids = tuple(part.strip() for part in raw.split(",") if part.strip())
            invalid = [subcategory_id for subcategory_id in ids if not subcategory_id.isdigit()]
            if invalid:
                raise ValueError(
                    "DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS must contain numeric IDs only; "
                    f"invalid={invalid}"
                )
            return ids
        return (_SUBCATEGORY_IDS["player_points"],)

    def _subcategory_chain_for_market(self, market: CanonicalMarket) -> tuple[str, ...]:
        """Return ordered subcategory IDs to try for this canonical market."""

        if market == "player_points":
            return self._player_points_subcategory_chain()
        return (_SUBCATEGORY_IDS[market],)

    async def __aenter__(self) -> "DraftKingsScraper":
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://sportsbook.draftkings.com/leagues/basketball/nba",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            ),
        }
        self._client = httpx.AsyncClient(headers=headers, timeout=30.0)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_events(self) -> list[OddsEvent]:
        # Some DK subcategory IDs intermittently return empty events.
        # Probe multiple known market subcategories and union event lists.
        events_by_id: dict[str, OddsEvent] = {}
        subcategory_ids = set(_SUBCATEGORY_IDS.values())
        subcategory_ids.update(self._player_points_subcategory_chain())
        for subcategory_id in sorted(subcategory_ids):
            try:
                payload = await self._fetch_market_payload(subcategory_id=subcategory_id)
            except Exception as exc:
                logger.warning(
                    "draftkings_list_events_subcategory_failed",
                    subcategory_id=subcategory_id,
                    error=str(exc),
                )
                continue
            extracted = _extract_events_from_payload(payload)
            logger.info(
                "draftkings_events_extracted",
                subcategory_id=subcategory_id,
                extracted_events=len(extracted),
                has_events_key="events" in payload,
                has_eventgroup_key="eventGroup" in payload,
                has_attachments_key="attachments" in payload,
                market_types=_market_type_summary(payload),
            )
            for event in extracted:
                events_by_id[event.bookmaker_event_id] = event

        if events_by_id:
            return list(events_by_id.values())

        # Additional fallback: ask the same nash endpoint for league events without
        # subcategory filtering. This can still return upcoming games when individual
        # prop subcategory payloads have empty events arrays.
        try:
            payload = await self._fetch_market_payload_once()
            extracted = _extract_events_from_payload(payload)
            logger.info(
                "draftkings_events_extracted_fallback_all_events",
                extracted_events=len(extracted),
                has_events_key="events" in payload,
                has_eventgroup_key="eventGroup" in payload,
                has_attachments_key="attachments" in payload,
            )
            for event in extracted:
                events_by_id[event.bookmaker_event_id] = event
        except Exception as exc:
            logger.warning("draftkings_list_events_fallback_all_failed", error=str(exc))

        if events_by_id:
            return list(events_by_id.values())

        # Broad fallback: eventgroup endpoint can list events even when
        # leagueSubcategory market payloads are sparse.
        try:
            payload = await self._fetch_eventgroup_payload()
            for event in _extract_events_from_payload(payload):
                events_by_id[event.bookmaker_event_id] = event
        except Exception as exc:
            logger.warning("draftkings_list_events_eventgroup_failed", error=str(exc))
        return list(events_by_id.values())

    async def fetch_player_props(self, event: OddsEvent, market: CanonicalMarket) -> list[PropOffer]:
        # Primary path: event-specific endpoint with confirmed classic O/U subcategory IDs.
        event_subcat_id = self._event_subcategory_id_for_market(market)
        if not event_subcat_id:
            logger.info(
                "draftkings_market_unsupported_event_subcat",
                market=market,
                event_id=event.bookmaker_event_id,
                message="No eventSubcategory id configured for this canonical market; skipping.",
            )
            return []
        if event_subcat_id:
            try:
                payload = await self._fetch_event_subcat_payload(
                    event.bookmaker_event_id, event_subcat_id
                )
                offers = _parse_draftkings_player_props(payload, event, market)
                if offers:
                    raw_path = self._save_raw(payload, event.bookmaker_event_id, market)
                    for offer in offers:
                        offer.raw_response_path = str(raw_path)
                    logger.info(
                        "draftkings_event_subcat_offers",
                        market=market,
                        event_id=event.bookmaker_event_id,
                        subcategory_id=event_subcat_id,
                        offer_count=len(offers),
                    )
                    return offers
                logger.debug(
                    "draftkings_event_subcat_empty",
                    market=market,
                    event_id=event.bookmaker_event_id,
                    subcategory_id=event_subcat_id,
                    markets_count=len(payload.get("markets") or []),
                )
                logger.info(
                    "draftkings_event_subcat_no_offers",
                    market=market,
                    event_id=event.bookmaker_event_id,
                    subcategory_id=event_subcat_id,
                )
                return []
            except Exception as exc:
                logger.warning(
                    "draftkings_event_subcat_failed",
                    market=market,
                    event_id=event.bookmaker_event_id,
                    subcategory_id=event_subcat_id,
                    error=str(exc),
                )
                return []

        # Fallback: league endpoint (milestone subcategories — correctly returns 0 props).
        chain = self._subcategory_chain_for_market(market)
        payload_league: dict[str, Any] | None = None
        offers: list[PropOffer] = []
        active_subcategory_id = chain[0]
        for subcategory_id in chain:
            active_subcategory_id = subcategory_id
            cached = self._market_payload_cache.get(subcategory_id)
            if cached is None:
                cached = await self._fetch_market_payload(subcategory_id=subcategory_id)
                self._market_payload_cache[subcategory_id] = cached
            payload_league = cached
            offers = _parse_draftkings_player_props(cached, event, market)
            if offers:
                break

        if payload_league is None:
            return []

        payload = payload_league
        raw_path = self._save_raw(payload, event.bookmaker_event_id, market)
        if not offers:
            _log_payload_shape(payload=payload, market=market, stage="primary")
            for entity in ("markets", "selections"):
                variant_payload = await self._fetch_market_payload_variant(
                    subcategory_id=active_subcategory_id, entity=entity
                )
                variant_raw_path = self._save_raw(variant_payload, event.bookmaker_event_id, market)
                offers = _parse_draftkings_player_props(variant_payload, event, market)
                if offers:
                    raw_path = variant_raw_path
                    logger.info(
                        "draftkings_offer_parse_recovered_via_entity_variant",
                        market=market,
                        subcategory_id=active_subcategory_id,
                        entity=entity,
                        offer_count=len(offers),
                    )
                    break
                _log_payload_shape(payload=variant_payload, market=market, stage=f"variant_{entity}")
        if not offers:
            logger.info(
                "draftkings_no_classic_player_props",
                market=market,
                event_id=event.bookmaker_event_id,
                subcategory_chain=chain,
            )
        for offer in offers:
            offer.raw_response_path = str(raw_path)
        return offers

    async def fetch_all_moneyline_odds(self, events: list[OddsEvent]) -> list[MoneylineOffer]:
        return []

    def _event_subcategory_id_for_market(self, market: CanonicalMarket) -> str:
        """Return DraftKings eventSubcategory ID for *market*.

        Combo markets are configured via environment variables so that subcategory
        drift does not require code edits.
        """
        settings = get_settings()
        overrides: dict[CanonicalMarket, str] = {
            "player_points_rebounds_assists": settings.draftkings_event_subcat_player_points_rebounds_assists.strip(),
            "player_points_rebounds": settings.draftkings_event_subcat_player_points_rebounds.strip(),
            "player_points_assists": settings.draftkings_event_subcat_player_points_assists.strip(),
            "player_rebounds_assists": settings.draftkings_event_subcat_player_rebounds_assists.strip(),
        }
        override = overrides.get(market, "")
        if override:
            return override
        return _EVENT_SUBCATEGORY_IDS.get(market, "")

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _fetch_event_subcat_payload(self, event_id: str, subcategory_id: str) -> dict[str, Any]:
        """Fetch classic O/U player props from the event-specific eventSubcategory endpoint."""
        if self._client is None:
            raise RuntimeError("DraftKingsScraper must be used in async context.")
        template_vars = quote(f"{event_id},{subcategory_id}", safe="")
        markets_query = quote(
            f"$filter=eventId eq '{event_id}' AND clientMetadata/subCategoryId eq '{subcategory_id}'"
            f" AND tags/all(t: t ne 'SportcastBetBuilder')",
            safe="",
        )
        url = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "event/eventSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={template_vars}"
            f"&marketsQuery={markets_query}&entity=markets"
        )
        response = await self._client.get(url)
        logger.info("draftkings_event_subcat_request", status_code=response.status_code, url=url)
        response.raise_for_status()
        return response.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _fetch_market_payload(self, *, subcategory_id: str | None = None) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("DraftKingsScraper must be used in async context.")
        if subcategory_id is None:
            events_query = quote(f"$filter=leagueId eq '{_NBA_LEAGUE_ID}'", safe="")
            url = (
                "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
                "league/leagueSubcategory/v1/markets"
                f"?isBatchable=false&templateVars={_NBA_LEAGUE_ID}&eventsQuery={events_query}"
                "&include=Events&entity=events"
            )
        else:
            events_query = quote(
                f"$filter=leagueId eq '{_NBA_LEAGUE_ID}' AND clientMetadata/Subcategories/any(s: s/Id eq '{subcategory_id}')",
                safe="",
            )
            markets_query = quote(
                f"$filter=clientMetadata/subCategoryId eq '{subcategory_id}' AND tags/all(t: t ne 'SportcastBetBuilder')",
                safe="",
            )
            url = (
                "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
                "league/leagueSubcategory/v1/markets"
                f"?isBatchable=false&templateVars={_NBA_LEAGUE_ID}&eventsQuery={events_query}"
                f"&marketsQuery={markets_query}&include=Events&entity=events"
            )
        response = await self._client.get(url)
        logger.info("draftkings_request", status_code=response.status_code, url=url)
        response.raise_for_status()
        return response.json()

    async def _fetch_market_payload_once(self) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("DraftKingsScraper must be used in async context.")
        events_query = quote(f"$filter=leagueId eq '{_NBA_LEAGUE_ID}'", safe="")
        url = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "league/leagueSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={_NBA_LEAGUE_ID}&eventsQuery={events_query}"
            "&include=Events&entity=events"
        )
        response = await self._client.get(url)
        logger.info("draftkings_request", status_code=response.status_code, url=url)
        response.raise_for_status()
        return response.json()

    async def _fetch_market_payload_variant(self, *, subcategory_id: str, entity: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("DraftKingsScraper must be used in async context.")
        events_query = quote(
            f"$filter=leagueId eq '{_NBA_LEAGUE_ID}' AND clientMetadata/Subcategories/any(s: s/Id eq '{subcategory_id}')",
            safe="",
        )
        markets_query = quote(
            f"$filter=clientMetadata/subCategoryId eq '{subcategory_id}' AND tags/all(t: t ne 'SportcastBetBuilder')",
            safe="",
        )
        include = quote("Events,Markets,Selections", safe=",")
        url = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "league/leagueSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={_NBA_LEAGUE_ID}&eventsQuery={events_query}"
            f"&marketsQuery={markets_query}&include={include}&entity={entity}"
        )
        response = await self._client.get(url)
        logger.info("draftkings_request", status_code=response.status_code, url=url)
        response.raise_for_status()
        return response.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _fetch_eventgroup_payload(self) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("DraftKingsScraper must be used in async context.")
        url = f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/{_NBA_LEAGUE_ID}?format=json"
        response = await self._client.get(url)
        logger.info("draftkings_eventgroup_request", status_code=response.status_code, url=url)
        response.raise_for_status()
        return response.json()

    def _save_raw(self, payload: dict[str, Any], event_id: str, market: CanonicalMarket) -> Path:
        now = datetime.now(timezone.utc)
        day_dir = self._raw_root / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{event_id}_{market}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _markets_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index markets[*] by id."""

    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("markets") or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or row.get("marketId") or "")
        if mid:
            out[mid] = row
    return out


def _dk_market_row_is_milestone(market: dict[str, Any]) -> bool:
    """Return True when DK marks this market row as a milestone ladder."""

    mt = market.get("marketType")
    if isinstance(mt, dict):
        mid = str(mt.get("id") or "")
        if mid in _DK_MILESTONE_MARKET_TYPE_IDS:
            return True
        name = str(mt.get("name") or "").lower()
        if "milestone" in name:
            return True
    return False


def _dk_selection_has_milestone_shape(selection: dict[str, Any]) -> bool:
    """Detect milestone ladder selections that must never become synthetic O/U rows."""

    if selection.get("milestoneValue") is not None:
        return True
    label = str(selection.get("label") or "").strip()
    return bool(label) and label.endswith("+")


def _market_type_summary(payload: dict[str, Any]) -> list[dict[str, str | int]]:
    """Summarize DK marketType IDs/names for endpoint discovery logs."""

    summary: dict[tuple[str, str], int] = {}
    markets = payload.get("markets", [])
    if not isinstance(markets, list):
        return []
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_type = market.get("marketType")
        if not isinstance(market_type, dict):
            continue
        market_type_id = str(market_type.get("id") or "")
        market_type_name = str(market_type.get("name") or "")
        if not market_type_id and not market_type_name:
            continue
        key = (market_type_id, market_type_name)
        summary[key] = summary.get(key, 0) + 1
    return [
        {"id": market_type_id, "name": market_type_name, "count": count}
        for (market_type_id, market_type_name), count in sorted(summary.items())
    ]


def _parse_draftkings_player_props(
    payload: dict[str, Any], event: OddsEvent, market: CanonicalMarket
) -> list[PropOffer]:
    """Parse DK market payload into PropOffer rows."""

    allowed_market_ids = _market_ids_for_event(payload, event.bookmaker_event_id)
    if not allowed_market_ids and _payload_has_event_scoped_markets(payload):
        return []
    market_lines = _market_line_by_id(payload)
    markets_by_id = _markets_by_id(payload)
    outcomes: dict[str, dict[str, Any]] = {}
    for selection in _iter_selection_candidates(payload):
        if not isinstance(selection, dict):
            continue
        if allowed_market_ids and not _selection_in_allowed_markets(selection, allowed_market_ids):
            continue
        market_id = _selection_market_id(selection)
        if market_id is not None:
            row = markets_by_id.get(str(market_id))
            if row is not None and _dk_market_row_is_milestone(row):
                continue
        if _dk_selection_has_milestone_shape(selection):
            continue
        side = _extract_selection_side(selection)
        if side is None:
            continue
        participant = _extract_selection_player_name(selection)
        fallback_key = market_id if market_id is not None else ""
        line = _extract_selection_line(selection, fallback_line=market_lines.get(fallback_key))
        if not participant or line is None:
            continue
        key = f"{participant}|{line}"
        price = _extract_selection_price(selection)
        if price is None:
            continue
        bucket = outcomes.setdefault(
            key,
            {
                "name": participant,
                "line": line,
                "_explicit_over": False,
                "_explicit_under": False,
            },
        )
        bucket[f"{side}_price"] = price
        if side == "over":
            bucket["_explicit_over"] = True
        elif side == "under":
            bucket["_explicit_under"] = True
    offers: list[PropOffer] = []
    now = datetime.now(timezone.utc)
    for bucket in outcomes.values():
        if "over_price" not in bucket or "under_price" not in bucket or not bucket["name"]:
            continue
        if bucket["over_price"] == bucket["under_price"] and not (
            bucket.get("_explicit_over") and bucket.get("_explicit_under")
        ):
            continue
        offers.append(
            PropOffer(
                bookmaker_key=event.bookmaker_key,
                bookmaker_event_id=event.bookmaker_event_id,
                home_team_name=event.home_team_name,
                away_team_name=event.away_team_name,
                commence_time=event.commence_time,
                raw_player_name=str(bucket["name"]),
                market=market,
                line=float(bucket["line"]),
                over_price=int(bucket["over_price"]),
                under_price=int(bucket["under_price"]),
                last_update=None,
                scraped_at=now,
                explicit_over_under=True,
            )
        )
    return offers


def _iter_selection_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    selections: list[dict[str, Any]] = []
    top_level = payload.get("selections", [])
    if isinstance(top_level, list):
        selections.extend(item for item in top_level if isinstance(item, dict))

    for market in payload.get("markets", []):
        if not isinstance(market, dict):
            continue
        market_id = str(market.get("id") or market.get("marketId") or "")
        for key in ("selections", "outcomes", "options"):
            nested = market.get(key, [])
            if not isinstance(nested, list):
                continue
            for item in nested:
                if not isinstance(item, dict):
                    continue
                if market_id and "marketId" not in item and "market_id" not in item:
                    item = dict(item)
                    item["marketId"] = market_id
                selections.append(item)
    return selections


def _market_ids_for_event(payload: dict[str, Any], event_id: str) -> set[str]:
    market_ids: set[str] = set()
    markets = payload.get("markets", [])
    if not isinstance(markets, list):
        return market_ids
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_event_id = str(
            market.get("eventId")
            or market.get("event_id")
            or market.get("event")
            or market.get("providerEventId")
            or ""
        )
        if market_event_id and market_event_id != event_id:
            continue
        market_id = str(market.get("id") or market.get("marketId") or "")
        if market_id:
            market_ids.add(market_id)
    return market_ids


def _payload_has_event_scoped_markets(payload: dict[str, Any]) -> bool:
    markets = payload.get("markets", [])
    if not isinstance(markets, list):
        return False
    for market in markets:
        if not isinstance(market, dict):
            continue
        if any(market.get(key) for key in ("eventId", "event_id", "event", "providerEventId")):
            return True
    return False


def _selection_in_allowed_markets(selection: dict[str, Any], allowed_market_ids: set[str]) -> bool:
    candidate_id = _selection_market_id(selection)
    if candidate_id is not None:
        return str(candidate_id) in allowed_market_ids
    return False


def _extract_selection_side(selection: dict[str, Any]) -> str | None:
    texts = [
        str(selection.get("label") or ""),
        str(selection.get("name") or ""),
        str(selection.get("outcomeType") or ""),
        str(selection.get("selectionType") or ""),
        str(selection.get("resultType") or ""),
    ]
    for text in texts:
        lower = text.strip().lower()
        if "over" in lower:
            return "over"
        if "under" in lower:
            return "under"
        if lower in {"o", "ov", "over."}:
            return "over"
        if lower in {"u", "un", "under."}:
            return "under"
    return None


def _extract_selection_line(selection: dict[str, Any], *, fallback_line: float | None = None) -> float | None:
    for key in ("points", "line", "handicap", "value"):
        raw_value = selection.get(key)
        if raw_value is None:
            continue
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            continue
    label = str(selection.get("label") or "")
    match = re.search(r"(-?\d+(?:\.\d+)?)", label)
    if match:
        return float(match.group(1))
    if fallback_line is not None:
        return fallback_line
    return None


def _extract_selection_player_name(selection: dict[str, Any]) -> str:
    participants = selection.get("participants", [])
    if isinstance(participants, list):
        for participant in participants:
            if isinstance(participant, dict):
                name = str(participant.get("name") or "").strip()
                if name:
                    return name
    for key in ("playerName", "participantName", "runnerName", "name"):
        raw_value = selection.get(key)
        name = str(raw_value or "").strip()
        if name.lower() in {"over", "under", "o", "u"}:
            continue
        if name:
            return name
    return ""


def _extract_selection_price(selection: dict[str, Any]) -> int | None:
    def _coerce_odds(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip().replace("−", "-").replace("+", "")
            if not cleaned:
                return None
            try:
                return int(cleaned)
            except ValueError:
                return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    display_odds = selection.get("displayOdds", {})
    if isinstance(display_odds, dict):
        american = display_odds.get("american")
        if american is not None:
            coerced = _coerce_odds(american)
            if coerced is not None:
                return coerced
    odds = selection.get("odds", {})
    if isinstance(odds, dict):
        for key in ("american", "americanOdds", "oddsAmerican", "price"):
            value = odds.get(key)
            if value is None:
                continue
            coerced = _coerce_odds(value)
            if coerced is not None:
                return coerced
    for key in ("americanOdds", "oddsAmerican", "price"):
        value = selection.get(key)
        if value is None:
            continue
        coerced = _coerce_odds(value)
        if coerced is not None:
            return coerced
    return None


def _selection_market_id(selection: dict[str, Any]) -> str | None:
    for key in ("marketId", "market_id", "market", "marketTypeId"):
        value = selection.get(key)
        if value is not None:
            return str(value)
    return None


def _market_line_by_id(payload: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    markets = payload.get("markets", [])
    if not isinstance(markets, list):
        return out
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_id = str(market.get("id") or market.get("marketId") or "")
        if not market_id:
            continue
        line: float | None = None
        for key in ("line", "points", "handicap", "value", "threshold"):
            raw_value = market.get(key)
            if raw_value is None:
                continue
            try:
                line = float(raw_value)
                break
            except (TypeError, ValueError):
                continue
        if line is not None:
            out[market_id] = line
    return out


def _log_payload_shape(*, payload: dict[str, Any], market: CanonicalMarket, stage: str) -> None:
    markets = payload.get("markets", [])
    selections = payload.get("selections", [])
    events = payload.get("events", [])
    first_market_keys: list[str] = []
    first_selection_keys: list[str] = []
    first_market_name = ""
    first_market_subcategory = ""
    first_selection_label = ""
    first_selection_milestone: float | None = None
    first_selection_participant = ""
    if isinstance(markets, list) and markets and isinstance(markets[0], dict):
        first_market_keys = sorted(str(k) for k in markets[0].keys())[:20]
        first_market_name = str(markets[0].get("name") or "")
        first_market_subcategory = str(markets[0].get("subcategoryId") or "")
    if isinstance(selections, list) and selections and isinstance(selections[0], dict):
        first_selection_keys = sorted(str(k) for k in selections[0].keys())[:30]
        first = selections[0]
        first_selection_label = str(first.get("label") or "")
        milestone = first.get("milestoneValue")
        try:
            first_selection_milestone = float(milestone) if milestone is not None else None
        except (TypeError, ValueError):
            first_selection_milestone = None
        participants = first.get("participants", [])
        if isinstance(participants, list) and participants and isinstance(participants[0], dict):
            first_selection_participant = str(participants[0].get("name") or "")
    logger.info(
        "draftkings_payload_shape",
        market=market,
        stage=stage,
        top_level_keys=sorted(str(k) for k in payload.keys())[:30],
        events_count=len(events) if isinstance(events, list) else -1,
        markets_count=len(markets) if isinstance(markets, list) else -1,
        selections_count=len(selections) if isinstance(selections, list) else -1,
        first_market_keys=first_market_keys,
        first_market_name=first_market_name,
        first_market_subcategory=first_market_subcategory,
        first_selection_keys=first_selection_keys,
        first_selection_label=first_selection_label,
        first_selection_milestone=first_selection_milestone,
        first_selection_participant=first_selection_participant,
    )


def _extract_events_from_payload(payload: dict[str, Any]) -> list[OddsEvent]:
    events: list[OddsEvent] = []
    for event in _iter_raw_events(payload):
        if not isinstance(event, dict):
            continue
        start = _parse_datetime(
            event.get("startDate")
            or event.get("startDateTime")
            or event.get("eventStartDate")
            or event.get("eventStartDateTime")
            or event.get("startEventDate")
            or event.get("startEventDateTime")
            or event.get("startTime")
        )
        if start is None:
            continue
        bookmaker_event_id = str(event.get("eventId") or event.get("id") or "")
        home_team = str(
            event.get("teamName2")
            or event.get("homeTeamName")
            or event.get("homeTeam", {}).get("name", "")
        )
        away_team = str(
            event.get("teamName1")
            or event.get("awayTeamName")
            or event.get("awayTeam", {}).get("name", "")
        )
        if not home_team or not away_team:
            home_team, away_team = _extract_team_names_from_participants(event)
        if not bookmaker_event_id or not home_team or not away_team:
            continue
        events.append(
            OddsEvent(
                bookmaker_key="draftkings",
                bookmaker_event_id=bookmaker_event_id,
                home_team_name=home_team,
                away_team_name=away_team,
                commence_time=start,
            )
        )
    return events


def _iter_raw_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_events = payload.get("events")
    if isinstance(raw_events, list):
        return [event for event in raw_events if isinstance(event, dict)]
    if isinstance(raw_events, dict):
        out: list[dict[str, Any]] = []
        for value in raw_events.values():
            if isinstance(value, dict):
                nested = value.get("event")
                if isinstance(nested, dict):
                    out.append(nested)
                else:
                    out.append(value)
        return out

    event_group = payload.get("eventGroup")
    event_group_events = event_group.get("events", []) if isinstance(event_group, dict) else []
    if isinstance(event_group_events, list) and event_group_events:
        return [event for event in event_group_events if isinstance(event, dict)]
    if isinstance(event_group_events, dict):
        return [event for event in event_group_events.values() if isinstance(event, dict)]

    attachments_events = payload.get("attachments", {}).get("events", {})
    if isinstance(attachments_events, dict):
        return [event for event in attachments_events.values() if isinstance(event, dict)]
    return []


def _extract_team_names_from_participants(event: dict[str, Any]) -> tuple[str, str]:
    participants = event.get("participants")
    if not isinstance(participants, list):
        return "", ""
    home_team = ""
    away_team = ""
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        name = str(participant.get("name") or "").strip()
        if not name:
            continue
        venue_role = str(participant.get("venueRole") or "").strip().lower()
        if venue_role == "home":
            home_team = name
        elif venue_role == "away":
            away_team = name
    return home_team, away_team


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        raw = str(value).strip()
        raw = re.sub(r"\.(\d{6})\d+(?=[Zz]|[+-]\d{2}:\d{2}$)", r".\1", raw)
        return datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None
