"""Unit tests for injury scrapers."""

from __future__ import annotations

from pathlib import Path

from data_pipeline.scrapers.injuries import ESPNScraper, RotowireScraper

FIXTURE_ROOT = Path("tests/fixtures/injuries")


def test_parse_rotowire_full_slate() -> None:
    records = RotowireScraper().parse((FIXTURE_ROOT / "rotowire_sample.html").read_bytes())
    assert len(records) == 2


def test_parse_rotowire_partial_morning() -> None:
    records = RotowireScraper().parse((FIXTURE_ROOT / "rotowire_partial.html").read_bytes())
    assert len(records) == 1
    assert records[0].raw_status == "Probable"


def test_parse_espn_html() -> None:
    records = ESPNScraper().parse((FIXTURE_ROOT / "espn_sample.html").read_bytes())
    assert len(records) == 1
    assert records[0].raw_player_name == "Joel Embiid"
    assert records[0].team_abbr == "PHI"
    assert records[0].body_part == "Knee"


def test_parse_espn_api_news_feed() -> None:
    records = ESPNScraper().parse((FIXTURE_ROOT / "espn_api_sample.json").read_bytes())
    assert len(records) == 1
    assert records[0].raw_player_name == "Luka Doncic"
    assert records[0].raw_status == "questionable"


def test_browser_fallback_triggers_on_cloudflare_marker() -> None:
    marker = b"<html>Attention Required! | Cloudflare</html>"
    assert b"cloudflare" in marker.lower()
