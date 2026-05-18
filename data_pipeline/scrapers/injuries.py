"""Scrapers for Rotowire and ESPN injury feeds."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict
from selectolax.parser import HTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from data_pipeline.config import get_settings
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.injuries_endpoints import ESPN_NEWS_URL, ROTOWIRE_URL

logger = get_logger(__name__)


class InjuryRecord(BaseModel):
    """Normalized injury scrape row before DB loading."""

    model_config = ConfigDict(extra="forbid", strict=True)

    raw_player_name: str
    team_abbr: str | None = None
    raw_status: str
    body_part: str | None = None
    source: str
    scraped_at: datetime


class InjuryScraper(Protocol):
    """Protocol for injury source scrapers."""

    async def fetch(self) -> bytes: ...

    def parse(self, content: bytes) -> list[InjuryRecord]: ...


class _BaseScraper:
    """Common HTTP fetch and raw persistence behavior."""

    source: str
    url: str
    suffix: str

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def fetch(self) -> bytes:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(self.url)
            response.raise_for_status()
            content = response.content
            if b"cloudflare" in content.lower() or b"attention required" in content.lower():
                logger.warning("injury_cloudflare_detected", source=self.source, fallback="playwright_required")
            self._save_raw(content)
            return content

    def _save_raw(self, content: bytes) -> Path:
        root = get_settings().raw_storage_path / "injuries" / self.source
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = root / f"{stamp}.{self.suffix}"
        path.write_bytes(content)
        return path


class RotowireScraper(_BaseScraper):
    """Rotowire HTML injury/lineups scraper."""

    source = "rotowire"
    url = ROTOWIRE_URL
    suffix = "html"

    def parse(self, content: bytes) -> list[InjuryRecord]:
        html = HTMLParser(content.decode("utf-8", errors="ignore"))
        records: list[InjuryRecord] = []
        now = datetime.now(timezone.utc)
        for node in html.css(".lineup__player"):
            name_node = node.css_first(".lineup__player-name")
            status_node = node.css_first(".lineup__injury-status")
            if name_node is None or status_node is None:
                continue
            records.append(
                InjuryRecord(
                    raw_player_name=name_node.text(strip=True),
                    raw_status=status_node.text(strip=True),
                    source=self.source,
                    scraped_at=now,
                )
            )
        return records


class ESPNScraper(_BaseScraper):
    """ESPN HTML injuries scraper."""

    source = "espn"
    url = ESPN_NEWS_URL
    suffix = "json"

    def parse(self, content: bytes) -> list[InjuryRecord]:
        text = content.decode("utf-8", errors="ignore")
        if text.lstrip().startswith("{"):
            return self._parse_api_json(text)
        html = HTMLParser(text)
        records: list[InjuryRecord] = []
        now = datetime.now(timezone.utc)
        for row in html.css("tr.Table__TR"):
            cols = row.css("td")
            if len(cols) < 4:
                continue
            records.append(
                InjuryRecord(
                    raw_player_name=cols[0].text(strip=True),
                    team_abbr=cols[1].text(strip=True) if len(cols) > 1 else None,
                    body_part=cols[2].text(strip=True) if len(cols) > 2 else None,
                    raw_status=cols[3].text(strip=True),
                    source=self.source,
                    scraped_at=now,
                )
            )
        return records

    def _parse_api_json(self, content: str) -> list[InjuryRecord]:
        import json
        from re import search

        payload = json.loads(content)
        now = datetime.now(timezone.utc)
        records: list[InjuryRecord] = []
        for article in payload.get("articles", []):
            headline = str(article.get("headline", ""))
            description = str(article.get("description", ""))
            # Lightweight fallback extractor for "Name questionable/out/probable..." patterns.
            match = search(
                r"^(?P<name>[A-Za-z\.\-\'\s]+?)\s+(?P<status>questionable|out|doubtful|probable|available)\b",
                headline,
                flags=0,
            )
            if not match:
                continue
            records.append(
                InjuryRecord(
                    raw_player_name=match.group("name").strip(),
                    raw_status=match.group("status"),
                    body_part=description or None,
                    source=self.source,
                    scraped_at=now,
                )
            )
        return records


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Injury source scraper")
    parser.add_argument("--source", choices=["rotowire", "espn", "all"], required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch", action="store_true")
    return parser


async def _run_once(source: str) -> None:
    scrapers: list[InjuryScraper]
    if source == "rotowire":
        scrapers = [RotowireScraper()]
    elif source == "espn":
        scrapers = [ESPNScraper()]
    else:
        scrapers = [RotowireScraper(), ESPNScraper()]
    for scraper in scrapers:
        content = await scraper.fetch()
        records = scraper.parse(content)
        logger.info("injury_scrape_snapshot", source=getattr(scraper, "source"), records=len(records))


async def _run_cli() -> None:
    args = _build_parser().parse_args()
    if args.watch and args.once:
        raise SystemExit("Choose only one of --once or --watch.")
    if args.watch:
        while True:
            await _run_once(args.source)
            await asyncio.sleep(900)
    await _run_once(args.source)


if __name__ == "__main__":
    asyncio.run(_run_cli())
