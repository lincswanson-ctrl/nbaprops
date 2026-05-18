"""Tests for dashboard DB query helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any

from dashboard import queries


class _FakeCursor:
    def __init__(self, rows_by_call: list[list[dict[str, Any]]]) -> None:
        self._rows_by_call = rows_by_call
        self._idx = -1

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def execute(self, query: str, params: dict[str, Any]) -> None:
        del query, params
        self._idx += 1

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows_by_call[self._idx]

    def fetchone(self) -> dict[str, Any]:
        return self._rows_by_call[self._idx][0]


class _FakeConn:
    def __init__(self, rows_by_call: list[list[dict[str, Any]]]) -> None:
        self._rows_by_call = rows_by_call

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows_by_call)


@contextmanager
def _fake_get_connection(rows_by_call: list[list[dict[str, Any]]]) -> Any:
    yield _FakeConn(rows_by_call)


def test_get_picks_returns_rows(monkeypatch: Any) -> None:
    rows = [
        [
            {
                "pick_id": 1,
                "game_id": "001",
                "market": "player_points",
                "side": "over",
                "line": 23.5,
                "edge_pct": 0.05,
                "confidence": "medium",
                "status": "active",
                "model_version": "naive_v1",
                "home_team": "LAL",
                "away_team": "BOS",
            }
        ]
    ]
    monkeypatch.setattr(queries, "get_connection", lambda: _fake_get_connection(rows))
    result = queries.get_picks(date(2026, 5, 9))
    assert len(result) == 1
    assert result[0]["game_id"] == "001"


def test_get_summary_returns_expected_keys(monkeypatch: Any) -> None:
    rows = [
        [{"n": 4}],
        [{"n": 12}],
        [{"avg_edge": 0.033}],
        [{"n": 7}],
    ]
    monkeypatch.setattr(queries, "get_connection", lambda: _fake_get_connection(rows))
    result = queries.get_summary(date(2026, 5, 9))
    assert result == {
        "game_count": 4,
        "market_count": 7,
        "pick_count": 12,
        "avg_edge": 0.033,
    }


def test_get_available_pick_dates(monkeypatch: Any) -> None:
    rows = [[{"game_date": date(2026, 5, 9)}, {"game_date": date(2026, 5, 8)}]]
    monkeypatch.setattr(queries, "get_connection", lambda: _fake_get_connection(rows))
    result = queries.get_available_pick_dates()
    assert result == [date(2026, 5, 9), date(2026, 5, 8)]


def test_get_dates_with_edges_or_picks(monkeypatch: Any) -> None:
    rows = [[{"d": date(2026, 5, 9)}, {"d": date(2026, 5, 8)}]]
    monkeypatch.setattr(queries, "get_connection", lambda: _fake_get_connection(rows))
    result = queries.get_dates_with_edges_or_picks()
    assert result == [date(2026, 5, 9), date(2026, 5, 8)]


def test_get_team_moneyline_rows(monkeypatch: Any) -> None:
    rows = [[{"game_id": "001", "market": "moneyline", "edge_pct": 0.03}]]
    monkeypatch.setattr(queries, "get_connection", lambda: _fake_get_connection(rows))
    result = queries.get_team_moneyline_rows(date(2026, 5, 9))
    assert len(result) == 1
    assert result[0]["market"] == "moneyline"
