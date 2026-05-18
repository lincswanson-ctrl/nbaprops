"""Unit tests for officials resolution and loader logic."""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline.loaders.official_resolution import (
    _fuzzy_match,
    _initialed_match,
    normalize_official_name,
    resolve_official_name_with_cursor,
)
from data_pipeline.loaders.officials import (
    _canonical_team_label,
    _extract_boxscore_officials,
    _is_late_change,
    _resolve_box_crew_ids,
    _resolve_game_id,
    _resolve_crew_ids,
)
from data_pipeline.scrapers.officials import AssignmentRow

FIXTURE_ROOT = Path("tests/fixtures/officials")


class FakeCursor:
    """Tiny fake cursor for resolution tests."""

    def __init__(self, officials_rows: list[dict[str, object]] | None = None) -> None:
        self._officials = officials_rows or []
        self._fetchone_value: dict[str, object] | None = None
        self.executed: list[tuple[str, dict[str, object] | None]] = []

    def execute(self, query: str, params: dict[str, object] | None = None) -> None:
        self.executed.append((query, params))
        normalized = " ".join(query.split()).lower()
        if "select official_id, full_name from officials" in normalized:
            return
        if "select g.game_id" in normalized:
            self._fetchone_value = {"game_id": "0022500001"}
            return
        if "select game_id, crew_chief_id" in normalized:
            self._fetchone_value = None

    def fetchall(self) -> list[dict[str, object]]:
        return list(self._officials)

    def fetchone(self) -> dict[str, object] | None:
        return self._fetchone_value


def test_resolve_official_exact_match() -> None:
    cursor = FakeCursor([{"official_id": "114", "full_name": "Scott Foster"}])
    resolved = resolve_official_name_with_cursor(cursor, "Scott Foster")
    assert resolved.official_id == "114"


def test_resolve_official_initialed_match() -> None:
    rows = [{"official_id": "114", "full_name": "Scott Foster"}]
    matched = _initialed_match(rows, normalize_official_name("S. Foster"))
    assert matched is not None
    assert matched["official_id"] == "114"


def test_resolve_official_initialed_collision() -> None:
    rows = [
        {"official_id": "1", "full_name": "John Williams"},
        {"official_id": "2", "full_name": "James Williams"},
    ]
    matched = _initialed_match(rows, normalize_official_name("J. Williams"))
    assert matched is None


def test_resolve_official_creates_new() -> None:
    cursor = FakeCursor([])
    resolved = resolve_official_name_with_cursor(cursor, "Totally New Ref")
    assert resolved.full_name == "Totally New Ref"
    assert resolved.official_id.startswith("gen_")


def test_resolve_official_strips_suffix() -> None:
    assert normalize_official_name("Tony Brothers Jr.") == "tony brothers"


def test_loader_prospective_idempotent() -> None:
    cursor = FakeCursor([{"official_id": "114", "full_name": "Scott Foster"}])
    row = AssignmentRow(
        game_date=__import__("datetime").date(2025, 11, 15),
        game_label="Boston Celtics @ Los Angeles Lakers",
        crew_chief="Scott Foster",
        referee=None,
        umpire=None,
    )
    first = _resolve_crew_ids(cursor, row)
    second = _resolve_crew_ids(cursor, row)
    assert first == second


def test_loader_box_score_overrides_prospective() -> None:
    existing = {"crew_chief_id": "1", "referee_id": "2", "umpire_id": "3"}
    actual = {"crew_chief_id": "1", "referee_id": "2", "umpire_id": "3"}
    assert not _is_late_change(existing, actual)


def test_loader_detects_late_change() -> None:
    existing = {"crew_chief_id": "1", "referee_id": "2", "umpire_id": "3"}
    actual = {"crew_chief_id": "1", "referee_id": "2", "umpire_id": "9"}
    assert _is_late_change(existing, actual)


def test_loader_box_score_alone() -> None:
    payload = json.loads((FIXTURE_ROOT / "boxscore_summary_officials_sample.json").read_text(encoding="utf-8"))
    rows = _extract_boxscore_officials(payload)
    cursor = FakeCursor(
        [
            {"official_id": "114", "full_name": "Scott Foster"},
            {"official_id": "225", "full_name": "James Capers"},
            {"official_id": "311", "full_name": "Mark Lindsay"},
        ]
    )
    resolved = _resolve_box_crew_ids(cursor, rows)
    assert len(rows) == 3
    assert resolved["crew_chief_id"] == "114"


def test_team_name_resolution_for_game_lookup() -> None:
    cursor = FakeCursor()
    game_id = _resolve_game_id(
        cursor,
        __import__("datetime").date(2025, 11, 15),
        "Boston Celtics @ LA Lakers",
    )
    assert game_id == "0022500001"
    assert _canonical_team_label("LA Lakers") == "Los Angeles Lakers"


def test_fuzzy_match_threshold() -> None:
    rows = [{"official_id": "1", "full_name": "Scott Foster"}]
    matched = _fuzzy_match(rows, "scot foster")
    assert matched is not None
