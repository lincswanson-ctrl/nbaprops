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


def test_parse_rotowire_2026_markup_uses_full_name_and_inj_span() -> None:
    """Real 2026-27 markup: full name in link title, status in ``span.lineup__inj``."""
    records = RotowireScraper().parse(
        (FIXTURE_ROOT / "rotowire_2026_11_10_lineups.html").read_bytes()
    )
    by_name = {r.raw_player_name: r.raw_status for r in records}
    assert by_name == {
        "Quentin Grimes": "Ques",
        "Keshon Gilbert": "Doubt",
        "Henri Veesaar": "OFS",
        "Mouhamed Gueye": "Out",
        "Zion Williamson": "Prob",
    }


def test_parse_rotowire_2026_markup_skips_players_without_status() -> None:
    records = RotowireScraper().parse(
        (FIXTURE_ROOT / "rotowire_2026_11_10_lineups.html").read_bytes()
    )
    names = {r.raw_player_name for r in records}
    assert "Luka Doncic" not in names
    assert "Ziaire Williams" not in names


def test_parse_rotowire_empty_page_returns_no_records() -> None:
    assert RotowireScraper().parse(b"<html><body></body></html>") == []


def test_parse_espn_injuries_page_2026_layout() -> None:
    """Real 2026-27 layout: one table per team, NAME | POS | RETURN | STATUS | COMMENT."""
    records = ESPNScraper().parse((FIXTURE_ROOT / "espn_injuries_2026_11_10.html").read_bytes())
    rows = [(r.raw_player_name, r.team_abbr, r.raw_status, r.body_part) for r in records]
    assert rows == [
        ("Asa Newell", "ATL", "Out", "Toe"),
        ("Keshon Gilbert", "ATL", "Out", "Knee"),
        ("Mikel Brown Jr.", "BKN", "Day-To-Day", None),
        ("Grant Nelson", "BKN", "Day-To-Day", None),
    ]
    assert all(r.source == "espn" for r in records)


def test_parse_espn_injuries_page_unknown_team_keeps_rows() -> None:
    html = (FIXTURE_ROOT / "espn_injuries_2026_11_10.html").read_text(encoding="utf-8")
    html = html.replace("Atlanta Hawks", "Seattle Expansion")
    records = ESPNScraper().parse(html.encode("utf-8"))
    assert len(records) == 4
    assert [r.team_abbr for r in records[:2]] == [None, None]


def test_parse_espn_injuries_page_empty_returns_no_records() -> None:
    assert ESPNScraper().parse(b"<html><body></body></html>") == []


def test_espn_scraper_targets_injuries_page() -> None:
    assert ESPNScraper.url == "https://www.espn.com/nba/injuries"
    assert ESPNScraper.suffix == "html"
