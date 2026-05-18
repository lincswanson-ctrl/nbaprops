"""Scrape NBA referee assignment announcements."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from selectolax.parser import HTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from data_pipeline.config import get_settings
from data_pipeline.logging import get_logger

logger = get_logger(__name__)
OFFICIAL_URL = "https://official.nba.com/referee-assignments/"


@dataclass(frozen=True)
class AssignmentRow:
    """Parsed row from official NBA assignment table."""

    game_date: date
    game_label: str
    crew_chief: str | None
    referee: str | None
    umpire: str | None


class NBAOfficialsScraper:
    """Fetch and parse official NBA referee assignments page."""

    def __init__(self) -> None:
        self._raw_root = get_settings().raw_storage_path / "officials" / "nba_official"

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def fetch_assignments(self, target_date: date) -> bytes:
        """Fetch assignment HTML and persist raw response before parsing."""

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.nba.com/",
            },
        ) as client:
            response = await client.get(OFFICIAL_URL)
            body = response.content
            if response.status_code >= 400 or _looks_like_blocked(body):
                body = await self._fetch_with_playwright()
            self._save_raw(target_date, body)
            return body

    def parse_assignments(self, content: bytes, target_date: date) -> list[AssignmentRow]:
        """Parse rows for the requested date from assignments HTML."""

        html = HTMLParser(content.decode("utf-8", errors="ignore"))
        rows: list[AssignmentRow] = []
        for row in html.css("table tr"):
            cells = [cell.text(strip=True) for cell in row.css("td")]
            if len(cells) < 4:
                continue
            parsed_date = _parse_mdy(cells[0])
            if parsed_date is None or parsed_date != target_date:
                continue
            game_label = cells[1]
            if "@" not in game_label or "tbd" in game_label.lower():
                continue
            rows.append(
                AssignmentRow(
                    game_date=target_date,
                    game_label=game_label,
                    crew_chief=_clean_name(cells[2]),
                    referee=_clean_name(cells[3]),
                    umpire=_clean_name(cells[4]) if len(cells) > 4 else None,
                )
            )
        return [row for row in rows if row.crew_chief or row.referee]

    async def _fetch_with_playwright(self) -> bytes:
        """Fallback browser fetch for anti-bot pages."""

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise httpx.HTTPError("Playwright fallback required but playwright is not installed.") from exc

        async with async_playwright() as playwright:  # pragma: no cover - network dependent
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(OFFICIAL_URL, wait_until="networkidle", timeout=45000)
            content = (await page.content()).encode("utf-8")
            await browser.close()
            return content

    def _save_raw(self, target_date: date, content: bytes) -> Path:
        day_root = self._raw_root / target_date.isoformat()
        day_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = day_root / f"{stamp}.html"
        path.write_bytes(content)
        return path


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.replace("\xa0", " ").strip().split())
    if not cleaned or cleaned.lower() in {"tbd", "-", "—"}:
        return None
    return cleaned


def _parse_mdy(value: str) -> date | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _looks_like_blocked(content: bytes) -> bool:
    lowered = content.lower()
    return b"cloudflare" in lowered or b"attention required" in lowered


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape official NBA referee assignments")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--date", help="Specific date in YYYY-MM-DD")
    return parser


def _within_watch_window(now_utc: datetime) -> bool:
    et_hour = now_utc.astimezone(ZoneInfo("America/New_York")).hour
    return 9 <= et_hour <= 13


async def _run_once(scraper: NBAOfficialsScraper, target_date: date) -> None:
    content = await scraper.fetch_assignments(target_date)
    rows = scraper.parse_assignments(content, target_date)
    logger.info("officials_scrape_snapshot", date=target_date.isoformat(), rows=len(rows))


async def _run_cli() -> None:
    args = _build_parser().parse_args()
    if args.watch and args.once:
        raise SystemExit("Choose only one of --once or --watch.")
    target_date = date.fromisoformat(args.date) if args.date else date.today()
    scraper = NBAOfficialsScraper()
    if args.watch:
        while True:
            now_utc = datetime.now(UTC)
            if _within_watch_window(now_utc):
                await _run_once(scraper, date.today())
            await asyncio.sleep(int(timedelta(minutes=60).total_seconds()))
        return
    await _run_once(scraper, target_date)


if __name__ == "__main__":
    asyncio.run(_run_cli())
