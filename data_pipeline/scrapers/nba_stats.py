"""stats.nba.com scraper with raw response persistence."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from data_pipeline.config import get_settings
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.nba_stats_endpoints import BASE_URL, ENDPOINTS, NBA_STATS_HEADERS

logger = get_logger(__name__)


class RateLimiter:
    """Simple async token bucket for global requests-per-minute limiting."""

    def __init__(self, rate_per_minute: int) -> None:
        self._interval = 60.0 / max(rate_per_minute, 1)
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def wait(self) -> None:
        """Wait until the next request is allowed."""

        async with self._lock:
            now = time.monotonic()
            wait_for = self._interval - (now - self._last_call)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_call = time.monotonic()


@dataclass(frozen=True)
class FetchResult:
    """Metadata for one raw fetch operation."""

    endpoint: str
    raw_file_path: Path
    fetched_at: datetime


class NBAStatsScraper:
    """Async scraper for stats.nba.com endpoints."""

    def __init__(self) -> None:
        settings = get_settings()
        self._raw_root = settings.raw_storage_path / "nba_stats"
        self._rate_limiter = RateLimiter(settings.nba_stats_rate_limit_per_min)
        self._semaphore = asyncio.Semaphore(5)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NBAStatsScraper":
        self._client = httpx.AsyncClient(headers=NBA_STATS_HEADERS, timeout=30.0)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None

    async def fetch_leaguegamefinder(self, season: str) -> FetchResult:
        return await self._fetch_and_store(
            endpoint="leaguegamefinder",
            season=season,
            natural_key=f"{season}_games",
            params={
                "LeagueID": "00",
                "SeasonType": "Regular Season",
                "Season": season,
            },
        )

    async def fetch_boxscoretraditionalv2(self, season: str, game_id: str) -> FetchResult:
        return await self._fetch_boxscore_endpoint("boxscoretraditionalv2", season, game_id)

    async def fetch_boxscoreadvancedv2(self, season: str, game_id: str) -> FetchResult:
        return await self._fetch_boxscore_endpoint("boxscoreadvancedv2", season, game_id)

    async def fetch_boxscoreplayertrackv2(self, season: str, game_id: str) -> FetchResult:
        return await self._fetch_boxscore_endpoint("boxscoreplayertrackv2", season, game_id)

    async def fetch_boxscorematchupsv3(self, season: str, game_id: str) -> FetchResult:
        return await self._fetch_boxscore_endpoint("boxscorematchupsv3", season, game_id)

    async def fetch_playergamelogs(self, season: str) -> FetchResult:
        return await self._fetch_and_store(
            endpoint="playergamelogs",
            season=season,
            natural_key=f"{season}_player_logs",
            params={"Season": season, "SeasonType": "Regular Season", "PlayerOrTeam": "P"},
        )

    async def fetch_commonplayerinfo(self, season: str, player_id: str) -> FetchResult:
        return await self._fetch_and_store(
            endpoint="commonplayerinfo",
            season=season,
            natural_key=player_id,
            params={"PlayerID": player_id},
        )

    async def fetch_leaguedashplayerstats(self, season: str) -> FetchResult:
        return await self._fetch_and_store(
            endpoint="leaguedashplayerstats",
            season=season,
            natural_key=f"{season}_player_dash",
            params={"Season": season, "SeasonType": "Regular Season"},
        )

    async def fetch_scoreboard(self, season: str, game_date: date) -> FetchResult:
        return await self._fetch_and_store(
            endpoint="scoreboardv2",
            season=season,
            natural_key=game_date.isoformat(),
            params={"GameDate": game_date.strftime("%m/%d/%Y"), "LeagueID": "00", "DayOffset": 0},
        )

    async def fetch_game_bundle(
        self,
        season: str,
        game_id: str,
        *,
        include_optional_endpoints: bool = True,
    ) -> list[FetchResult]:
        """Fetch all per-game endpoints required for core loader tables."""

        task_specs: list[tuple[str, bool, asyncio.Future[FetchResult] | Any]] = [
            ("boxscoretraditionalv2", True, self.fetch_boxscoretraditionalv2(season, game_id)),
            ("boxscoreadvancedv2", True, self.fetch_boxscoreadvancedv2(season, game_id)),
        ]
        if include_optional_endpoints:
            # These endpoints are useful but not critical for core table loads.
            task_specs.extend(
                [
                    ("boxscoreplayertrackv2", False, self.fetch_boxscoreplayertrackv2(season, game_id)),
                    ("boxscorematchupsv3", False, self.fetch_boxscorematchupsv3(season, game_id)),
                ]
            )
        results = await asyncio.gather(*(spec[2] for spec in task_specs), return_exceptions=True)
        out: list[FetchResult] = []
        for (endpoint, required, _), result in zip(task_specs, results, strict=True):
            if isinstance(result, Exception):
                if required:
                    raise result
                logger.warning(
                    "nba_stats_non_blocking_endpoint_failed",
                    endpoint=endpoint,
                    game_id=game_id,
                    season=season,
                    error=str(result),
                )
                continue
            out.append(result)
        return out

    async def _fetch_boxscore_endpoint(self, endpoint: str, season: str, game_id: str) -> FetchResult:
        return await self._fetch_and_store(
            endpoint=endpoint,
            season=season,
            natural_key=game_id,
            params={"GameID": game_id},
        )

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _request_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("NBAStatsScraper must be used as an async context manager.")
        async with self._semaphore:
            await self._rate_limiter.wait()
            started = time.perf_counter()
            url = f"{BASE_URL}/{ENDPOINTS[endpoint].path}"
            try:
                response = await self._client.get(url, params=params)
            except httpx.ReadError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                logger.warning(
                    "nba_stats_read_error",
                    endpoint=endpoint,
                    url=url,
                    latency_ms=latency_ms,
                    params=params,
                    error=str(exc),
                    exc_info=True,
                )
                raise
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "nba_stats_request",
                endpoint=endpoint,
                status_code=response.status_code,
                latency_ms=latency_ms,
                response_size=len(response.content),
                params=params,
            )
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            return response.json()

    async def _fetch_and_store(
        self,
        *,
        endpoint: str,
        season: str,
        natural_key: str,
        params: dict[str, Any],
    ) -> FetchResult:
        payload = await self._request_json(endpoint=endpoint, params=params)
        raw_path = self._raw_root / endpoint / season / f"{natural_key}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return FetchResult(endpoint=endpoint, raw_file_path=raw_path, fetched_at=datetime.now(timezone.utc))


def parse_boxscore_traditional_players(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse minimal player rows from boxscoretraditionalv2 response payload."""

    result_sets = payload.get("resultSets", [])
    player_stats = next(
        (result for result in result_sets if isinstance(result, dict) and result.get("name") == "PlayerStats"),
        None,
    )
    if player_stats is None:
        return []
    headers = player_stats.get("headers", [])
    idx = {name: i for i, name in enumerate(headers)}
    rows: list[dict[str, Any]] = []
    for row in player_stats.get("rowSet", []):
        comment = str(row[idx["COMMENT"]]) if "COMMENT" in idx else ""
        minutes_raw = str(row[idx["MIN"]]) if "MIN" in idx else "0:00"
        rows.append(
            {
                "player_id": str(row[idx["PLAYER_ID"]]),
                "team_id": str(row[idx["TEAM_ID"]]),
                "minutes": 0.0 if minutes_raw.startswith("0:") else _minutes_to_decimal(minutes_raw),
                "points": int(row[idx["PTS"]]) if "PTS" in idx else 0,
                "rebounds": int(row[idx["REB"]]) if "REB" in idx else 0,
                "assists": int(row[idx["AST"]]) if "AST" in idx else 0,
                "starter": bool(str(row[idx.get("START_POSITION", -1)]).strip()),
                "dnp_reason": comment or None,
            }
        )
    return rows


def _minutes_to_decimal(value: str) -> float:
    pieces = value.split(":")
    if len(pieces) != 2:
        return 0.0
    minutes = int(pieces[0])
    seconds = int(pieces[1])
    return round(minutes + (seconds / 60.0), 3)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape stats.nba.com data")
    parser.add_argument("--season", required=True, help="NBA season label, e.g. 2024-25")
    parser.add_argument("--start-date", help="Optional start date YYYY-MM-DD")
    parser.add_argument("--end-date", help="Optional end date YYYY-MM-DD")
    parser.add_argument(
        "--endpoint",
        help="Optional endpoint mode: all, core, or single endpoint name.",
        default="all",
    )
    parser.add_argument(
        "--max-games",
        type=int,
        help="Optional cap on number of game bundles when endpoint=all.",
    )
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="Only fetch core per-game endpoints (traditional + advanced).",
    )
    return parser


async def _run_cli() -> None:
    args = _build_parser().parse_args()
    async with NBAStatsScraper() as scraper:
        game_finder: FetchResult | None = None
        if args.endpoint in ("all", "core", "leaguegamefinder"):
            game_finder = await scraper.fetch_leaguegamefinder(args.season)
        if args.endpoint in ("all", "playergamelogs"):
            await scraper.fetch_playergamelogs(args.season)
        if args.endpoint in ("all", "leaguedashplayerstats"):
            try:
                await scraper.fetch_leaguedashplayerstats(args.season)
            except httpx.HTTPError as exc:
                # This endpoint intermittently returns 500 from stats.nba.com.
                # Treat it as non-blocking so the core per-game pipeline continues.
                logger.warning(
                    "nba_stats_non_blocking_endpoint_failed",
                    endpoint="leaguedashplayerstats",
                    season=args.season,
                    error=str(exc),
                )
        if args.start_date:
            start_date = date.fromisoformat(args.start_date)
            end_date = date.fromisoformat(args.end_date) if args.end_date else start_date
            current = start_date
            while current <= end_date:
                await scraper.fetch_scoreboard(args.season, current)
                current = date.fromordinal(current.toordinal() + 1)
        if args.endpoint in ("all", "core") and game_finder is not None:
            payload = json.loads(game_finder.raw_file_path.read_text(encoding="utf-8"))
            game_ids = _extract_game_ids(payload)
            if args.max_games is not None:
                game_ids = game_ids[: max(args.max_games, 0)]
            for game_id in game_ids:
                try:
                    await scraper.fetch_game_bundle(
                        args.season,
                        game_id,
                        include_optional_endpoints=not args.core_only,
                    )
                except httpx.HTTPError as exc:
                    logger.warning(
                        "nba_stats_game_bundle_failed",
                        season=args.season,
                        game_id=game_id,
                        error=str(exc),
                    )
    logger.info("nba_stats_scrape_complete", season=args.season, endpoint=args.endpoint)


def _extract_game_ids(payload: dict[str, Any]) -> list[str]:
    """Extract game ids from leaguegamefinder response payload."""

    result_sets = payload.get("resultSets", [])
    if not result_sets:
        return []
    first = result_sets[0]
    if not isinstance(first, dict):
        return []
    headers = first.get("headers", [])
    if "GAME_ID" not in headers:
        return []
    idx = headers.index("GAME_ID")
    game_ids: set[str] = set()
    for row in first.get("rowSet", []):
        if idx < len(row):
            game_ids.add(str(row[idx]))
    return sorted(game_ids)


if __name__ == "__main__":
    asyncio.run(_run_cli())
