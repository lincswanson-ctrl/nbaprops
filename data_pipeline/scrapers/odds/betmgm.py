"""BetMGM odds scraper."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from data_pipeline.config import get_settings
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.odds.base import (
    CanonicalMarket,
    MoneylineOffer,
    OddsEvent,
    PropOffer,
    normalize_market_name,
    normalize_team_abbr,
)

logger = get_logger(__name__)

_BETMGM_OPTION_LINE_RE = re.compile(r"\b(?:over|under)\s+(-?\d+(?:\.\d+)?)\b", re.IGNORECASE)


class BetMGMScraper:
    """Scrape BetMGM fixture/offer endpoints."""

    bookmaker_key = "betmgm"

    def __init__(self) -> None:
        settings = get_settings()
        self._raw_root = settings.raw_storage_path / "odds" / self.bookmaker_key
        self._base_url = settings.betmgm_base_url.rstrip("/")
        self._access_id = settings.betmgm_access_id
        self._subdivision = settings.betmgm_subdivision
        self._client: httpx.AsyncClient | None = None
        self._option_markets_by_fixture_id: dict[str, list[dict[str, Any]]] = {}

    async def __aenter__(self) -> "BetMGMScraper":
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self._base_url}/en/sports/basketball-7/betting/usa-9/nba-6004",
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
        payload = await self._request_json(
            (
                f"{self._base_url}/cds-api/bettingoffer/fixtures?state=Latest&fixtureTypes=Standard"
                "&tagTypes=Sport&sportIds=7&competitionIds=6004"
                f"&x-bwin-accessid={self._access_id}&lang=en-us&country=US&userCountry=US"
                f"&subdivision={self._subdivision}"
            )
        )
        fixtures = payload.get("fixtures", [])
        if isinstance(fixtures, list):
            for fixture in fixtures:
                if not isinstance(fixture, dict):
                    continue
                fixture_id = str(fixture.get("id") or fixture.get("fixtureId") or "")
                option_markets = fixture.get("optionMarkets", [])
                if fixture_id and isinstance(option_markets, list):
                    self._option_markets_by_fixture_id[fixture_id] = [
                        market for market in option_markets if isinstance(market, dict)
                    ]
        return _parse_betmgm_events(payload)

    async def fetch_player_props(self, event: OddsEvent, market: CanonicalMarket) -> list[PropOffer]:
        option_markets = self._option_markets_by_fixture_id.get(event.bookmaker_event_id)
        if option_markets is None or not option_markets:
            view_payload = await self._request_json(
                _fixture_view_url(
                    self._base_url, self._access_id, event.bookmaker_event_id, self._subdivision
                )
            )
            fixture = view_payload.get("fixture", {})
            option_markets_raw = fixture.get("optionMarkets", [])
            option_markets = (
                [row for row in option_markets_raw if isinstance(row, dict)]
                if isinstance(option_markets_raw, list)
                else []
            )
            self._option_markets_by_fixture_id[event.bookmaker_event_id] = option_markets

        selected_ids = set(_select_option_market_ids(option_markets or [], market))
        filtered = (
            [row for row in (option_markets or []) if str(row.get("id") or "") in selected_ids]
            if selected_ids
            else []
        )
        payload = {"fixture": {"id": event.bookmaker_event_id, "participants": [], "optionMarkets": filtered}}
        raw_path = self._save_raw(payload, event.bookmaker_event_id, market)
        offers = _parse_betmgm_player_props(payload, event, market)
        for offer in offers:
            offer.raw_response_path = str(raw_path)
        return offers

    async def fetch_all_moneyline_odds(self, events: list[OddsEvent]) -> list[MoneylineOffer]:
        offers: list[MoneylineOffer] = []
        for event in events:
            payload = await self._request_json(
                _fixture_view_url(self._base_url, self._access_id, event.bookmaker_event_id, self._subdivision)
            )
            raw_path = self._save_raw(payload, event.bookmaker_event_id, "moneyline")
            parsed = _parse_betmgm_moneyline_offers(payload, event, str(raw_path))
            offers.extend(parsed)
        return offers

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _request_json(self, url: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("BetMGMScraper must be used in async context.")
        response = await self._client.get(url)
        logger.info("betmgm_request", status_code=response.status_code, url=url)
        if _looks_like_bot_block(response):
            logger.warning("betmgm_cloudflare_detected", url=url)
            return await self._request_json_playwright(url)
        response.raise_for_status()
        return response.json()

    async def _request_json_playwright(self, url: str) -> dict[str, Any]:
        """Playwright fallback hook for bot-protected pages.

        Current implementation reuses httpx because browser automation is optional in local
        environments. Keeping the method boundary allows swapping in real Playwright quickly.
        """
        if self._client is None:
            raise RuntimeError("BetMGMScraper must be used in async context.")
        response = await self._client.get(url)
        response.raise_for_status()
        return response.json()

    def _save_raw(self, payload: dict[str, Any], event_id: str, market: str) -> Path:
        now = datetime.now(timezone.utc)
        day_dir = self._raw_root / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{event_id}_{market}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _fixture_view_url(
    base_url: str, access_id: str, fixture_id: str, subdivision: str
) -> str:
    return (
        f"{base_url}/cds-api/bettingoffer/fixture-view?x-bwin-accessid={access_id}&lang=en-us"
        f"&country=US&userCountry=US&subdivision={subdivision}&offerMapping=All"
        f"&scoreboardMode=Full&fixtureIds={fixture_id}&state=Latest"
        "&includePrecreatedBetBuilder=true&supportVirtual=true&isBettingInsightsEnabled=true"
        "&useRegionalisedConfiguration=true&includeRelatedFixtures=false&statisticsModes=All"
        "&firstMarketGroupOnly=false"
    )


def _fixture_offers_url(
    *,
    base_url: str,
    access_id: str,
    fixture_id: str,
    subdivision: str,
    option_market_ids: list[str],
) -> str:
    joined = ",".join(option_market_ids)
    return (
        f"{base_url}/cds-api/bettingoffer/fixture-offers?x-bwin-accessid={access_id}&lang=en-us"
        f"&country=US&userCountry=US&subdivision={subdivision}&fixtureIds={fixture_id}"
        f"&optionMarketIds={joined}&gameIds=&offerMapping=Filtered"
    )


def _select_option_market_ids(option_markets: list[dict[str, Any]], market: CanonicalMarket) -> list[str]:
    targets = _market_search_tokens(market)
    selected: list[str] = []
    for row in option_markets:
        raw_name = _name_value(row.get("name")).strip()
        if not raw_name:
            continue
        prefix, sep, market_name = raw_name.partition(":")
        market_part = market_name.strip() if sep else raw_name
        if market.startswith("player_"):
            # Player props are namespaced as "<Player (TEAM)>: <Market>".
            if not sep or "(" not in prefix:
                continue
        lower = market_part.lower()
        if not any(token in lower for token in targets):
            continue
        if market == "player_points" and any(token in lower for token in ("assists", "rebounds")):
            continue
        if market == "player_rebounds" and any(token in lower for token in ("assists", "points")):
            continue
        if market == "player_assists" and any(token in lower for token in ("rebounds", "points")):
            continue
        mid = str(row.get("id") or "")
        if not mid:
            continue
        selected.append(mid)
    # Bound the request size; BetMGM optionMarketIds can be huge.
    return selected[:80]


def _parse_betmgm_events(payload: dict[str, Any]) -> list[OddsEvent]:
    """Parse BetMGM fixture listing into OddsEvent rows."""
    now = datetime.now(timezone.utc)
    events: list[OddsEvent] = []
    for fixture in payload.get("fixtures", []):
        competition = str(
            fixture.get("competition", {}).get("name", {}).get("value")
            or fixture.get("competition", {}).get("name")
            or ""
        ).lower()
        if competition and "nba" not in competition:
            continue
        start = _parse_datetime(fixture.get("startDate"))
        if start is None or start < now:
            continue
        participants = fixture.get("participants", [])
        if len(participants) < 2:
            continue
        away = _participant_name(participants[0])
        home = _participant_name(participants[1])
        if not away or not home:
            continue
        events.append(
            OddsEvent(
                bookmaker_key="betmgm",
                bookmaker_event_id=str(fixture.get("id") or fixture.get("fixtureId") or ""),
                home_team_name=home,
                away_team_name=away,
                commence_time=start,
            )
        )
    return [event for event in events if event.bookmaker_event_id]


def _parse_betmgm_player_props(
    payload: dict[str, Any], event: OddsEvent, market: CanonicalMarket
) -> list[PropOffer]:
    """Parse BetMGM fixture payload into player props.

    BetMGM's `fixture-view` payload often ships with `fixture.optionMarkets` populated but
    `fixture.games` empty. The `optionMarkets[*].options` list provides explicit Over/Under
    options with American odds and is the most reliable parsing surface.
    """
    option_markets = _iter_option_markets(payload)
    if option_markets:
        return _parse_betmgm_option_markets_player_props(option_markets, event, market)

    targets = _market_search_tokens(market)
    games = _iter_games(payload)
    rows: list[PropOffer] = []
    scraped_at = datetime.now(timezone.utc)
    for game in games:
        game_name = _name_value(game.get("name")).lower()
        if not any(token in game_name for token in targets):
            continue
        pairings: dict[tuple[str, float], dict[str, Any]] = {}
        for result in game.get("results", []):
            label = _name_value(result.get("name"))
            raw_player_name = _extract_player_name(label, result)
            line = float(result.get("attr") or result.get("line") or result.get("points") or 0.0)
            key = (raw_player_name, line)
            side = _result_side(label)
            if side is None:
                continue
            price = _coerce_american_odds(result.get("americanOdds"))
            bucket = pairings.setdefault(key, {})
            bucket[side] = price
        for (raw_name, line), prices in pairings.items():
            if "over" not in prices or "under" not in prices or not raw_name:
                continue
            rows.append(
                PropOffer(
                    bookmaker_key=event.bookmaker_key,
                    bookmaker_event_id=event.bookmaker_event_id,
                    home_team_name=event.home_team_name,
                    away_team_name=event.away_team_name,
                    commence_time=event.commence_time,
                    raw_player_name=raw_name,
                    market=market,
                    line=line,
                    over_price=int(prices["over"]),
                    under_price=int(prices["under"]),
                    last_update=None,
                    scraped_at=scraped_at,
                    explicit_over_under=True,
                )
            )
    return rows


def _parse_betmgm_option_markets_player_props(
    option_markets: list[dict[str, Any]], event: OddsEvent, market: CanonicalMarket
) -> list[PropOffer]:
    """Parse `fixture.optionMarkets` into player O/U PropOffer rows."""
    targets = _market_search_tokens(market)
    scraped_at = datetime.now(timezone.utc)
    pairings: dict[tuple[str, float], dict[str, int]] = {}

    for row in option_markets:
        raw_name = _name_value(row.get("name")).strip()
        if not raw_name:
            continue
        prefix, sep, market_name = raw_name.partition(":")
        if not sep or "(" not in prefix:
            continue
        market_part = market_name.strip().lower()
        if not any(token in market_part for token in targets):
            continue
        if market == "player_points" and any(token in market_part for token in ("assists", "rebounds")):
            continue
        if market == "player_rebounds" and any(token in market_part for token in ("assists", "points")):
            continue
        if market == "player_assists" and any(token in market_part for token in ("rebounds", "points")):
            continue

        raw_player_name = prefix.strip()
        if " (" in raw_player_name and raw_player_name.endswith(")"):
            raw_player_name = raw_player_name.split("(", maxsplit=1)[0].strip()
        options_raw = row.get("options", [])
        if not isinstance(options_raw, list):
            continue
        for option in options_raw:
            if not isinstance(option, dict):
                continue
            label = _name_value(option.get("name")).strip()
            if not label:
                continue
            side = _result_side(label) or str(option.get("totalsPrefix") or "").strip().lower()
            if side not in {"over", "under"}:
                continue
            line = _extract_option_line(label, option, row)
            if line is None:
                continue
            price = option.get("price")
            american = None
            if isinstance(price, dict):
                american = price.get("americanOdds")
            odds = _coerce_american_odds(american)
            if odds == 0:
                continue
            bucket = pairings.setdefault((raw_player_name, float(line)), {})
            bucket[side] = int(odds)

    offers: list[PropOffer] = []
    for (player_name, line), prices in pairings.items():
        if "over" not in prices or "under" not in prices:
            continue
        offers.append(
            PropOffer(
                bookmaker_key=event.bookmaker_key,
                bookmaker_event_id=event.bookmaker_event_id,
                home_team_name=event.home_team_name,
                away_team_name=event.away_team_name,
                commence_time=event.commence_time,
                raw_player_name=player_name,
                market=market,
                line=float(line),
                over_price=int(prices["over"]),
                under_price=int(prices["under"]),
                last_update=None,
                scraped_at=scraped_at,
                explicit_over_under=True,
            )
        )
    return offers


def _extract_option_line(label: str, option: dict[str, Any], market: dict[str, Any]) -> float | None:
    """Extract the prop line from BetMGM option shapes."""
    for key in ("attr", "line", "points"):
        value = option.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    value = market.get("attr")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    match = _BETMGM_OPTION_LINE_RE.search(label)
    if match:
        return float(match.group(1))
    return None


def _parse_betmgm_moneyline_offers(
    payload: dict[str, Any], event: OddsEvent, raw_response_path: str | None
) -> list[MoneylineOffer]:
    """Parse BetMGM fixture payload into two-sided team moneyline offers."""
    games = _iter_games(payload)
    participants = _participant_map(payload)
    if not games or not participants:
        return []
    offers: list[MoneylineOffer] = []
    scraped_at = datetime.now(timezone.utc)
    for game in games:
        market_name = _name_value(game.get("name")).lower()
        if "moneyline" not in market_name and "money line" not in market_name:
            continue
        for result in game.get("results", []):
            participant_id = str(result.get("playerId") or result.get("participantId") or "")
            if participant_id not in participants:
                continue
            team_name = participants[participant_id]
            try:
                team = normalize_team_abbr(team_name)
            except ValueError:
                continue
            offers.append(
                MoneylineOffer(
                    bookmaker_key=event.bookmaker_key,
                    bookmaker_event_id=event.bookmaker_event_id,
                    home_team_name=event.home_team_name,
                    away_team_name=event.away_team_name,
                    commence_time=event.commence_time,
                    team=team,
                    moneyline_odds=_coerce_american_odds(result.get("americanOdds")),
                    scraped_at=scraped_at,
                    last_update=None,
                    raw_response_path=raw_response_path,
                )
            )
    return offers


def _participant_map(payload: dict[str, Any]) -> dict[str, str]:
    fixture = payload.get("fixture", {})
    participants = fixture.get("participants", [])
    lookup: dict[str, str] = {}
    for participant in participants:
        key = str(participant.get("participantId") or participant.get("id") or "")
        name = _participant_name(participant)
        if key and name:
            lookup[key] = name
    return lookup


def _iter_games(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fixture = payload.get("fixture", {})
    games = fixture.get("games", [])
    if isinstance(games, list):
        return [game for game in games if isinstance(game, dict)]
    return []


def _iter_option_markets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fixture = payload.get("fixture", {})
    markets = fixture.get("optionMarkets", [])
    if isinstance(markets, list):
        return [row for row in markets if isinstance(row, dict)]
    return []


def _name_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def _participant_name(participant: dict[str, Any]) -> str:
    return _name_value(participant.get("name")).strip()


def _result_side(label: str) -> str | None:
    lowered = label.lower()
    if lowered.startswith("over"):
        return "over"
    if lowered.startswith("under"):
        return "under"
    if " over " in lowered:
        return "over"
    if " under " in lowered:
        return "under"
    return None


def _extract_player_name(label: str, result: dict[str, Any]) -> str:
    player = _name_value(result.get("playerName")).strip()
    if player:
        return player
    base = label.strip()
    for prefix in ("Over ", "Under "):
        if base.startswith(prefix):
            remainder = base[len(prefix) :].strip()
            if " - " in remainder:
                return remainder.split(" - ", maxsplit=1)[0].strip()
            return remainder
    if " (" in base and base.endswith(")"):
        return base.split("(", maxsplit=1)[0].strip()
    return base


def _market_search_tokens(market: CanonicalMarket) -> tuple[str, ...]:
    mapping: dict[CanonicalMarket, tuple[str, ...]] = {
        "player_points": ("player points", "total points"),
        "player_rebounds": ("player rebounds", "total rebounds"),
        "player_assists": ("player assists", "total assists"),
        "player_threes": ("threes", "3-pointers"),
        "player_points_rebounds_assists": ("points + rebounds + assists", "total points, rebounds and assists", "pra"),
        "player_points_rebounds": ("points + rebounds", "total points and rebounds", "pts + rebs", "p+r"),
        "player_points_assists": ("points + assists", "total points and assists", "pts + ast", "p+a"),
        "player_rebounds_assists": ("rebounds + assists", "total rebounds and assists", "r+a"),
        "player_steals": ("steals",),
        "player_blocks": ("blocks",),
        "player_steals_blocks": ("steals + blocks",),
        "player_turnovers": ("turnovers",),
    }
    return mapping[market]


def _coerce_american_odds(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _looks_like_bot_block(response: httpx.Response) -> bool:
    if response.status_code in {403, 429, 503}:
        return True
    text = response.text.lower()
    return "cloudflare" in text or "attention required" in text or "cf-challenge" in text
