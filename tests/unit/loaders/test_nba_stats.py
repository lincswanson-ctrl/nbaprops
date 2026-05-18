"""Unit tests for NBA stats loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from data_pipeline.loaders.nba_stats import (
    NBAStatsLoader,
    _extract_games_from_scoreboard,
    _extract_lineups_from_matchups,
    _extract_player_stats_from_boxscore,
)

FIXTURE_ROOT = Path("tests/fixtures/nba_stats")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_loader_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_root = tmp_path / "nba_stats" / "scoreboardv2" / "2024-25"
    raw_root.mkdir(parents=True)
    sample = _read_json(FIXTURE_ROOT / "scoreboard_sample.json")
    raw_file = raw_root / "2024-10-22.json"
    raw_file.write_text(json.dumps(sample), encoding="utf-8")
    loader = NBAStatsLoader()
    monkeypatch.setattr(loader, "_raw_root", tmp_path / "nba_stats")
    seen: set[str] = set()

    def _fake_loaded(endpoint: str, natural_key: str, checksum: str) -> bool:
        key = f"{endpoint}:{natural_key}:{checksum}"
        if key in seen:
            return True
        seen.add(key)
        return False

    class Cursor:
        def __init__(self) -> None:
            self.calls = 0
            self._query_count = 0

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def executemany(self, query: str, rows: list[dict[str, Any]]) -> None:
            self.calls += len(rows)

        def execute(self, query: str, params: dict[str, Any]) -> None:
            self.calls += 1
            self._query_count += 1

        def fetchone(self) -> None:
            return None

    class Conn:
        def __init__(self, cursor: Cursor) -> None:
            self._cursor = cursor

        def __enter__(self) -> "Conn":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return self._cursor

    cursor = Cursor()

    from data_pipeline import loaders

    monkeypatch.setattr(loaders.nba_stats, "get_connection", lambda: Conn(cursor))
    monkeypatch.setattr(loaders.nba_stats, "_already_loaded", _fake_loaded)
    first = loader.load_scoreboard_schedule("2024-25", __import__("datetime").date(2024, 10, 22))
    second = loader.load_scoreboard_schedule("2024-25", __import__("datetime").date(2024, 10, 22))
    assert first.games_upserted == 1
    assert second.files_skipped == 1


def test_loader_handles_traded_player() -> None:
    payload = {
        "resultSets": [
            {
                "name": "PlayerStats",
                "headers": ["PLAYER_ID", "TEAM_ID", "MIN", "PTS", "REB", "AST", "START_POSITION", "COMMENT"],
                "rowSet": [
                    [1, 1610612737, "10:00", 4, 1, 1, "G", ""],
                    [1, 1610612738, "14:00", 6, 2, 2, "", ""],
                ],
            }
        ]
    }
    rows = _extract_player_stats_from_boxscore(payload, advanced_payload=None, game_id="0022400001")
    assert len(rows) == 2
    assert rows[0].player_id == rows[1].player_id
    assert rows[0].team_id != rows[1].team_id


def test_loader_rejects_malformed_response() -> None:
    malformed = {"resultSets": [{"name": "GameHeader", "headers": ["GAME_ID"], "rowSet": [["x"]]}]}
    with pytest.raises((ValidationError, KeyError)):
        _extract_games_from_scoreboard(malformed, "2024-25")


def test_loader_extracts_lineups_from_matchup_payload() -> None:
    payload = _read_json(FIXTURE_ROOT / "boxscorematchupsv3_lineups_sample.json")
    rows = _extract_lineups_from_matchups(payload, game_id="0022400001")
    assert len(rows) == 1
    assert rows[0].team_id == "1610612737"
    assert rows[0].possessions == 12.0

