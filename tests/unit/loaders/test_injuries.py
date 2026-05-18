"""Unit tests for injury mapping, resolution, and load behavior."""

from __future__ import annotations

from datetime import datetime, timezone

from data_pipeline.loaders.injury_status_mapping import normalize_status
from data_pipeline.loaders.injuries import InjuryLoader, _is_duplicate
from data_pipeline.loaders.name_resolution import _best_match, _suggest_player, normalize_name, resolve_player_name
from data_pipeline.scrapers.injuries import InjuryRecord
import pytest


def test_status_mapping_known_strings() -> None:
    assert normalize_status("Out") == "out"
    assert normalize_status("Questionable") == "questionable"


def test_status_mapping_unknown_falls_back() -> None:
    assert normalize_status("awaiting evaluation") == "unknown"


def test_status_mapping_case_insensitive() -> None:
    assert normalize_status("OUT") == "out"
    assert normalize_status("out") == "out"
    assert normalize_status("Out") == "out"


def test_name_resolution_exact_alias() -> None:
    assert normalize_name("LeBron James") == "lebron james"


def test_name_resolution_diacritics() -> None:
    assert normalize_name("Luka Dončić") == "luka doncic"


def test_name_resolution_team_scoped_fuzzy() -> None:
    assert normalize_name("L. James").startswith("l")


def test_name_resolution_unresolved_writes_to_table() -> None:
    assert normalize_name("Unknown Prospect") == "unknown prospect"


def test_name_resolution_best_match_returns_player_id_key() -> None:
    players = [
        {"player_id": "101108", "name": "Chris Paul"},
        {"player_id": "2544", "name": "LeBron James"},
    ]
    matched = _best_match("chris paul", players, 90)
    assert matched is not None
    assert matched["player_id"] == "101108"


def test_name_resolution_suggest_player_returns_player_id_key() -> None:
    players = [
        {"player_id": "101108", "name": "Chris Paul"},
        {"player_id": "2544", "name": "LeBron James"},
    ]
    suggested_player_id, confidence = _suggest_player("chris paul", players)
    assert suggested_player_id == "101108"
    assert confidence is not None
    assert confidence >= 90


def test_dedup_same_status_same_day_no_insert() -> None:
    now = datetime.now(timezone.utc)

    class Cur:
        def execute(self, query: str, params: dict[str, object]) -> None:
            return None

        def fetchone(self) -> dict[str, str]:
            return {"status": "questionable"}

    assert _is_duplicate(Cur(), "1", "rotowire", "questionable", now)


def test_dedup_status_change_creates_new_row() -> None:
    now = datetime.now(timezone.utc)

    class Cur:
        def execute(self, query: str, params: dict[str, object]) -> None:
            return None

        def fetchone(self) -> dict[str, str]:
            return {"status": "questionable"}

    assert not _is_duplicate(Cur(), "1", "rotowire", "out", now)


def test_loader_handles_traded_player() -> None:
    assert normalize_name("OG Anunoby") == "og anunoby"


def test_name_resolution_exact_alias_db_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cur:
        def __enter__(self) -> "Cur":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def execute(self, query: str, params: dict[str, object] | None = None) -> None:
            self._last = query

        def fetchone(self) -> dict[str, str] | None:
            if "player_name_aliases" in self._last:
                return {"player_id": "2544"}
            return None

        def fetchall(self) -> list[dict[str, object]]:
            return []

    class Conn:
        def __enter__(self) -> "Conn":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def cursor(self) -> Cur:
            return Cur()

    import data_pipeline.loaders.name_resolution as resolver

    monkeypatch.setattr(resolver, "get_connection", lambda: Conn())
    assert resolve_player_name("LeBron James", "LAL") == "2544"


def test_loader_snapshot_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    records = [
        InjuryRecord(raw_player_name="LeBron James", raw_status="Questionable", source="rotowire", scraped_at=now),
        InjuryRecord(raw_player_name="Unknown Name", raw_status="Out", source="rotowire", scraped_at=now),
    ]

    class Cur:
        def __enter__(self) -> "Cur":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def execute(self, query: str, params: dict[str, object]) -> None:
            self._query = query

        def fetchone(self) -> dict[str, str] | None:
            return None

    class Conn:
        def __enter__(self) -> "Conn":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def cursor(self) -> Cur:
            return Cur()

    import data_pipeline.loaders.injuries as injuries_loader

    monkeypatch.setattr(injuries_loader, "get_connection", lambda: Conn())
    monkeypatch.setattr(injuries_loader, "resolve_player_name", lambda name, team: "2544" if "LeBron" in name else None)
    summary = InjuryLoader().load_snapshot(records)
    assert summary.total_records == 2
    assert summary.inserted == 1
    assert summary.unresolved_names == 1
