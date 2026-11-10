"""Scrapers for Rotowire and ESPN injury feeds."""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from data_pipeline.config import get_settings
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.injuries_endpoints import ESPN_INJURIES_URL, ROTOWIRE_URL
from data_pipeline.scrapers.odds.base import normalize_team_abbr

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
                logger.warning(
                    "injury_cloudflare_detected", source=self.source, fallback="playwright_required"
                )
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
            name = _rotowire_player_name(node)
            status_node = node.css_first(".lineup__inj") or node.css_first(".lineup__injury-status")
            if name is None or status_node is None:
                continue
            records.append(
                InjuryRecord(
                    raw_player_name=name,
                    raw_status=status_node.text(strip=True),
                    source=self.source,
                    scraped_at=now,
                )
            )
        return records


def _rotowire_player_name(node: Node) -> str | None:
    """Return the full player name from a Rotowire ``.lineup__player`` node.

    Current markup puts the full name in the player link's ``title`` attribute (the link
    text is abbreviated, e.g. "Q. Grimes"); older markup used ``.lineup__player-name``.
    """
    link = node.css_first("a")
    if link is not None and (link.attributes.get("title") or "").strip():
        return str(link.attributes["title"]).strip()
    legacy = node.css_first(".lineup__player-name")
    return legacy.text(strip=True) if legacy is not None else None


class ESPNScraper(_BaseScraper):
    """ESPN injuries-page scraper (``espn.com/nba/injuries``), per spec 02.

    The news-feed JSON API is still parsed when given (tertiary sanity check), but the
    default fetch is the HTML injuries page, which lists every injured player per team.
    """

    source = "espn"
    url = ESPN_INJURIES_URL
    suffix = "html"

    def parse(self, content: bytes) -> list[InjuryRecord]:
        text = content.decode("utf-8", errors="ignore")
        if text.lstrip().startswith("{"):
            return self._parse_api_json(text)
        html = HTMLParser(text)
        now = datetime.now(timezone.utc)
        team_tables = html.css(".ResponsiveTable")
        if any(t.css_first(".injuries__teamName") is not None for t in team_tables):
            records: list[InjuryRecord] = []
            for table in team_tables:
                records.extend(_parse_espn_team_table(table, now))
            return records
        return _parse_espn_legacy_rows(html, now)

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


# ESPN injuries-page column order: NAME | POS | EST. RETURN DATE | STATUS | COMMENT
_ESPN_NAME_COL, _ESPN_STATUS_COL, _ESPN_COMMENT_COL = 0, 3, 4
# Comments read like "Nov 8: Newell (toe) has been ruled out ..."; the first parenthetical
# is the body part.
_ESPN_BODY_PART_RE = re.compile(r"\(([A-Za-z][A-Za-z \-/]{0,40})\)")


def _parse_espn_team_table(table: Node, scraped_at: datetime) -> list[InjuryRecord]:
    """Parse one team's table from the current ESPN injuries page layout."""
    team_node = table.css_first(".injuries__teamName")
    team_abbr = _espn_team_abbr(team_node.text(strip=True)) if team_node is not None else None
    records: list[InjuryRecord] = []
    for row in table.css("tbody tr"):
        cols = [cell.text(strip=True) for cell in row.css("td")]
        if len(cols) <= _ESPN_STATUS_COL or not cols[_ESPN_NAME_COL] or not cols[_ESPN_STATUS_COL]:
            continue
        comment = cols[_ESPN_COMMENT_COL] if len(cols) > _ESPN_COMMENT_COL else ""
        body_match = _ESPN_BODY_PART_RE.search(comment)
        records.append(
            InjuryRecord(
                raw_player_name=cols[_ESPN_NAME_COL],
                team_abbr=team_abbr,
                raw_status=cols[_ESPN_STATUS_COL],
                body_part=body_match.group(1).strip().title() if body_match else None,
                source="espn",
                scraped_at=scraped_at,
            )
        )
    return records


def _espn_team_abbr(team_name: str) -> str | None:
    """Map an ESPN full team name (e.g. "Atlanta Hawks") to its abbreviation."""
    try:
        return normalize_team_abbr(team_name)
    except ValueError:
        logger.warning("espn_unknown_team_name", team_name=team_name)
        return None


def _parse_espn_legacy_rows(html: HTMLParser, scraped_at: datetime) -> list[InjuryRecord]:
    """Parse the older flat layout: NAME | TEAM | BODY PART | STATUS."""
    records: list[InjuryRecord] = []
    for row in html.css("tr.Table__TR"):
        cols = row.css("td")
        if len(cols) < 4:
            continue
        records.append(
            InjuryRecord(
                raw_player_name=cols[0].text(strip=True),
                team_abbr=cols[1].text(strip=True),
                body_part=cols[2].text(strip=True),
                raw_status=cols[3].text(strip=True),
                source="espn",
                scraped_at=scraped_at,
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
        logger.info(
            "injury_scrape_snapshot", source=getattr(scraper, "source"), records=len(records)
        )


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
