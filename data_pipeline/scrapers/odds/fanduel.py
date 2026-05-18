"""FanDuel odds scraper."""

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
from data_pipeline.scrapers.odds.base import CanonicalMarket, OddsEvent, PropOffer

logger = get_logger(__name__)

_FANDUEL_MARKETS: dict[CanonicalMarket, str] = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "made threes",
    "player_points_rebounds_assists": "Points + Rebounds + Assists",
    "player_points_rebounds": "Points + Rebounds",
    "player_points_assists": "Points + Assists",
    "player_rebounds_assists": "Rebounds + Assists",
    "player_steals": "Steals",
    "player_blocks": "Blocks",
    "player_steals_blocks": "Steals + Blocks",
    "player_turnovers": "Turnovers",
}

_TO_SCORE_PTS_RE = re.compile(r"To Score (\d+)\+ Points", re.IGNORECASE)
_MADE_THREES_RE = re.compile(r"(\d+)\+ Made Threes", re.IGNORECASE)
_TO_RECORD_ASSISTS_RE = re.compile(r"To Record (\d+)\+ Assists", re.IGNORECASE)
_TO_RECORD_REB_RE = re.compile(r"To Record (\d+)\+ Rebounds", re.IGNORECASE)


class FanDuelScraper:
    """Scrape FanDuel NBA odds endpoints."""

    bookmaker_key = "fanduel"

    def __init__(self) -> None:
        settings = get_settings()
        self._raw_root = settings.raw_storage_path / "odds" / self.bookmaker_key
        self._sbapi_host = settings.fanduel_sbapi_host
        self._smp_host = settings.fanduel_smp_host
        self._api_key = settings.fanduel_api_key
        self._client: httpx.AsyncClient | None = None
        self._navigation_cache: dict[str, Any] | None = None

    async def __aenter__(self) -> "FanDuelScraper":
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://sportsbook.fanduel.com",
            "Referer": "https://sportsbook.fanduel.com/navigation/nba",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            ),
        }
        self._client = httpx.AsyncClient(headers=headers, timeout=60.0)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_events(self) -> list[OddsEvent]:
        nav = await self._fetch_navigation_payload()
        events_obj = nav.get("attachments", {}).get("events", {})
        events: list[OddsEvent] = []
        for raw in events_obj.values():
            if not isinstance(raw, dict):
                continue
            start = _parse_datetime(raw.get("openDate") or raw.get("startDate"))
            if start is None:
                continue
            name = str(raw.get("name") or "")
            teams = [part.strip() for part in name.split("@")]
            if len(teams) != 2:
                continue
            eid = raw.get("eventId") or raw.get("id")
            if eid is None:
                continue
            events.append(
                OddsEvent(
                    bookmaker_key=self.bookmaker_key,
                    bookmaker_event_id=str(eid),
                    away_team_name=teams[0],
                    home_team_name=teams[1],
                    commence_time=start,
                )
            )
        return events

    async def fetch_player_props(self, event: OddsEvent, market: CanonicalMarket) -> list[PropOffer]:
        nav = await self._fetch_navigation_payload()
        event_id = _fd_numeric_event_id(event.bookmaker_event_id)
        markets_map = nav.get("attachments", {}).get("markets", {})
        relevant = [
            m
            for m in markets_map.values()
            if isinstance(m, dict)
            and _fd_numeric_event_id(m.get("eventId")) == event_id
            and _fd_market_matches(market, m)
        ]
        price_payload = await self._fetch_market_prices(
            [_fd_numeric_market_id(m.get("marketId")) for m in relevant]
        )
        merged = _merge_fanduel_nav_and_prices(relevant, price_payload)
        markets_out: dict[str, Any] = {}
        for i, m in enumerate(merged):
            key = str(m.get("marketId") or f"idx_{i}")
            markets_out[key] = m
        slim = {
            "attachments": {"markets": markets_out},
            "fanduel_context": {"bookmaker_event_id": event.bookmaker_event_id, "market": market},
        }
        raw_path = self._save_raw(slim, event.bookmaker_event_id, market)
        offers = _parse_fanduel_attachment_markets(merged, event, market)
        if not offers:
            if relevant:
                logger.info(
                    "fanduel_no_classic_player_props",
                    market=market,
                    event_id=event.bookmaker_event_id,
                    nav_market_candidates=len(relevant),
                )
            else:
                logger.debug(
                    "fanduel_no_nav_markets_for_canonical",
                    market=market,
                    event_id=event.bookmaker_event_id,
                )
        for offer in offers:
            offer.raw_response_path = str(raw_path)
        return offers

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _fetch_navigation_payload(self) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("FanDuelScraper must be used in async context.")
        if self._navigation_cache is not None:
            return self._navigation_cache
        url = (
            f"https://{self._sbapi_host}/api/content-managed-page"
            "?page=CUSTOM&customPageId=nba&timezone=America%2FNew_York"
            f"&_ak={self._api_key}"
        )
        response = await self._client.get(url)
        logger.info("fanduel_navigation_request", status_code=response.status_code, url=url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("FanDuel navigation payload must be a JSON object.")
        self._navigation_cache = payload
        return payload

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _fetch_market_prices(self, market_ids: list[int | None]) -> list[dict[str, Any]]:
        """Fetch priced runner rows from FanDuel SMP (`getMarketPrices`)."""

        ids = sorted({mid for mid in market_ids if mid is not None})
        if not ids or self._client is None:
            return []
        url = (
            f"https://{self._smp_host}/api/sports/fixedodds/readonly/v1/getMarketPrices"
            "?priceHistory=0"
        )
        combined: list[dict[str, Any]] = []
        chunk_size = 40
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start : start + chunk_size]
            response = await self._client.post(url, json={"marketIds": chunk})
            logger.info(
                "fanduel_market_prices_request",
                status_code=response.status_code,
                chunk_size=len(chunk),
            )
            response.raise_for_status()
            combined.extend(_coerce_fanduel_price_payload(response.json()))
        return combined

    def _save_raw(self, payload: dict[str, Any], event_id: str, market: CanonicalMarket) -> Path:
        now = datetime.now(timezone.utc)
        day_dir = self._raw_root / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{event_id}_{market}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _fd_market_matches(canonical: CanonicalMarket, fd_market: dict[str, Any]) -> bool:
    """Return True if this FanDuel market row maps to our canonical prop type."""

    name = str(fd_market.get("marketName") or "")
    lower = name.lower()
    if canonical == "player_points":
        if lower == "total points":
            return False
        if _TO_SCORE_PTS_RE.search(name):
            return False
        return "player points" in lower
    if canonical == "player_threes":
        if _MADE_THREES_RE.search(name):
            return False
        return "made threes" in lower or "3-pointers made" in lower
    if canonical == "player_assists":
        if _TO_RECORD_ASSISTS_RE.search(name):
            return False
        return "player assists" in lower or ("assists" in lower and "player" in lower)
    if canonical == "player_rebounds":
        if _TO_RECORD_REB_RE.search(name):
            return False
        return "player rebounds" in lower or ("rebounds" in lower and "player" in lower)
    token = _FANDUEL_MARKETS[canonical].lower()
    return token in lower


def _fd_numeric_event_id(raw: Any) -> int:
    """Normalize FanDuel event identifiers that may appear as composite ids."""

    if raw is None:
        return 0
    text = str(raw).strip()
    if "." in text:
        text = text.split(".", 1)[0]
    try:
        return int(text)
    except ValueError:
        return 0


def _fd_numeric_market_id(raw: Any) -> int | None:
    """Normalize FanDuel market ids for SMP requests."""

    if raw is None:
        return None
    text = str(raw).strip()
    if "." in text:
        text = text.split(".", 1)[0]
    try:
        return int(text)
    except ValueError:
        return None


def _merge_fanduel_nav_and_prices(
    nav_rows: list[dict[str, Any]], priced: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Overlay SMP pricing onto navigation rows while preserving metadata."""

    if not priced:
        return nav_rows
    by_id: dict[int, dict[str, Any]] = {}
    for row in priced:
        mid = _fd_numeric_market_id(row.get("marketId"))
        if mid is not None:
            by_id[mid] = row
    merged: list[dict[str, Any]] = []
    for row in nav_rows:
        mid = _fd_numeric_market_id(row.get("marketId"))
        overlay = by_id.get(mid) if mid is not None else None
        if overlay is None:
            merged.append(row)
            continue
        base = dict(row)
        base.update(overlay)
        merged.append(base)
    return merged


def _coerce_fanduel_price_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize FanDuel SMP price payloads that may be lists or keyed maps."""

    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("markets", "marketPrices", "data", "results"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        if isinstance(rows, dict):
            return [row for row in rows.values() if isinstance(row, dict)]
    attachments = payload.get("attachments")
    if isinstance(attachments, dict):
        markets = attachments.get("markets")
        if isinstance(markets, list):
            return [row for row in markets if isinstance(row, dict)]
        if isinstance(markets, dict):
            return [row for row in markets.values() if isinstance(row, dict)]
    return [row for row in payload.values() if isinstance(row, dict)]


def _parse_fanduel_attachment_markets(
    fd_markets: list[dict[str, Any]], event: OddsEvent, market: CanonicalMarket
) -> list[PropOffer]:
    """Parse FanDuel markets into PropOffer rows (classic O/U only)."""

    payload = {"markets": fd_markets}
    return _parse_fanduel_player_props(payload, event, market)


def _parse_fanduel_player_props(
    payload: dict[str, Any], event: OddsEvent, market: CanonicalMarket
) -> list[PropOffer]:
    outcomes: dict[str, dict[str, Any]] = {}
    for market_obj in payload.get("markets", []):
        if not isinstance(market_obj, dict) or not _fd_market_matches(market, market_obj):
            continue
        for runner in market_obj.get("runners", []):
            if not isinstance(runner, dict):
                continue
            raw_name = _fd_runner_player_name(runner)
            side = _fd_runner_side(runner)
            line = _fd_runner_line(runner)
            price = _fd_runner_american_price(runner)
            if not raw_name or side is None or line is None or price is None:
                continue
            key = f"{raw_name}|{line}"
            bucket = outcomes.setdefault(key, {"name": raw_name, "line": line})
            if side == "over":
                bucket["over_price"] = price
            if side == "under":
                bucket["under_price"] = price
    now = datetime.now(timezone.utc)
    offers: list[PropOffer] = []
    for bucket in outcomes.values():
        if "over_price" not in bucket or "under_price" not in bucket:
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


def _fd_runner_side(runner: dict[str, Any]) -> str | None:
    """Extract an explicit over/under side from a FanDuel runner row."""

    result = runner.get("result")
    texts: list[str] = []
    if isinstance(result, dict):
        texts.append(str(result.get("type") or ""))
    for key in ("side", "outcomeType", "type", "selectionType", "runnerName", "name", "selectionName"):
        texts.append(str(runner.get(key) or ""))
    for text in texts:
        token = text.strip().lower()
        if token in {"yes", "over", "o"} or token.startswith("over "):
            return "over"
        if token in {"no", "under", "u"} or token.startswith("under "):
            return "under"
    return None


def _fd_runner_line(runner: dict[str, Any]) -> float | None:
    """Extract the prop line from handicap or common FanDuel labels."""

    for key in ("handicap", "line", "points"):
        raw_value = runner.get(key)
        if raw_value is None:
            continue
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            continue
    for key in ("runnerName", "name", "selectionName"):
        match = re.search(r"\b(?:Over|Under)\s+(-?\d+(?:\.\d+)?)\b", str(runner.get(key) or ""), re.I)
        if match:
            return float(match.group(1))
    return None


def _fd_runner_player_name(runner: dict[str, Any]) -> str:
    """Return player name, stripping O/U side and line prefixes when present."""

    for key in ("playerName", "participantName"):
        value = str(runner.get(key) or "").strip()
        if value:
            return value
    for key in ("runnerName", "name", "selectionName"):
        value = str(runner.get(key) or "").strip()
        if not value:
            continue
        cleaned = re.sub(r"^(?:Over|Under)\s+-?\d+(?:\.\d+)?\s+", "", value, flags=re.I).strip()
        cleaned = re.sub(r"^(?:Over|Under)\s+", "", cleaned, flags=re.I).strip()
        if cleaned:
            return cleaned
    return ""


def _fd_runner_american_price(runner: dict[str, Any]) -> int | None:
    """Extract American odds from FanDuel runner price shapes."""

    odds_block = runner.get("winRunnerOdds") or runner.get("odds") or {}
    if isinstance(odds_block, dict):
        amer = odds_block.get("americanDisplayOdds")
        if isinstance(amer, dict):
            for key in ("americanOddsInt", "americanOdds"):
                value = amer.get(key)
                if value is not None:
                    return int(value)
        for key in ("americanOddsInt", "americanOdds", "american", "price"):
            value = odds_block.get(key)
            if value is not None:
                return int(value)
    for key in ("americanOddsInt", "americanOdds", "american", "price"):
        value = runner.get(key)
        if value is not None:
            return int(value)
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
