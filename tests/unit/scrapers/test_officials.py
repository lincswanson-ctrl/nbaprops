"""Unit tests for officials scraper parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from data_pipeline.scrapers.officials import NBAOfficialsScraper

FIXTURE_ROOT = Path("tests/fixtures/officials")


def test_parse_assignments_full_table() -> None:
    scraper = NBAOfficialsScraper()
    rows = scraper.parse_assignments(
        (FIXTURE_ROOT / "nba_official_assignments_sample.html").read_bytes(),
        date(2025, 11, 15),
    )
    assert len(rows) == 2
    assert rows[0].crew_chief == "Scott Foster"


def test_parse_assignments_missing_umpire() -> None:
    scraper = NBAOfficialsScraper()
    rows = scraper.parse_assignments(
        (FIXTURE_ROOT / "nba_official_assignments_partial.html").read_bytes(),
        date(2025, 11, 15),
    )
    assert rows[0].umpire is None


def test_parse_assignments_partial_morning() -> None:
    scraper = NBAOfficialsScraper()
    rows = scraper.parse_assignments(
        (FIXTURE_ROOT / "nba_official_assignments_partial.html").read_bytes(),
        date(2025, 11, 15),
    )
    assert len(rows) == 1
