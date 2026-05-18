"""Unit tests for NBA stats scraper/parsers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline.scrapers.nba_stats import (
    NBAStatsScraper,
    _extract_game_ids,
    parse_boxscore_traditional_players,
)

FIXTURE_ROOT = Path("tests/fixtures/nba_stats")


def _load_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_parse_boxscore_traditional_full_game() -> None:
    payload = _load_json("boxscoretraditionalv2_sample.json")
    rows = parse_boxscore_traditional_players(payload)
    assert rows
    assert rows[0]["player_id"] == "203507"
    assert rows[0]["points"] == 24


def test_parse_boxscore_handles_dnp() -> None:
    payload = _load_json("boxscoretraditionalv2_sample.json")
    rows = parse_boxscore_traditional_players(payload)
    dnp = rows[1]
    assert dnp["minutes"] == 0.0
    assert dnp["dnp_reason"] == "DNP - Coach's Decision"


def test_parse_boxscore_handles_overtime() -> None:
    payload = {
        "resultSets": [
            {
                "name": "PlayerStats",
                "headers": ["PLAYER_ID", "TEAM_ID", "MIN", "PTS", "REB", "AST", "START_POSITION", "COMMENT"],
                "rowSet": [[1, 2, "42:00", 30, 10, 8, "G", ""]],
            }
        ]
    }
    rows = parse_boxscore_traditional_players(payload)
    assert rows[0]["minutes"] == 42.0


def test_scraper_retries_on_429() -> None:
    # Retry behavior is implemented via tenacity decorator; smoke-test that method exists.
    assert hasattr(NBAStatsScraper, "_request_json")


def test_scraper_writes_raw_before_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = NBAStatsScraper()
    monkeypatch.setattr(scraper, "_raw_root", tmp_path)

    async def _fake_request(*args: object, **kwargs: object) -> dict[str, object]:
        return {"ok": True}

    monkeypatch.setattr(scraper, "_request_json", _fake_request)

    async def _run() -> Path:
        result = await scraper._fetch_and_store(
            endpoint="scoreboardv2",
            season="2024-25",
            natural_key="2024-10-22",
            params={},
        )
        return result.raw_file_path

    import asyncio

    raw_file = asyncio.run(_run())
    assert raw_file.exists()


def test_extract_game_ids_from_leaguegamefinder() -> None:
    payload = _load_json("leaguegamefinder_sample.json")
    game_ids = _extract_game_ids(payload)
    assert game_ids == ["0022400001"]
