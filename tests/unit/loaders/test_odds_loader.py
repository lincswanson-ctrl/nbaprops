"""Unit tests for OddsLoader."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from data_pipeline.loaders.odds import OddsLoader
from data_pipeline.scrapers.odds.base import PropOffer


class _FakeCursor:
    def __init__(
        self, rowcounts: list[int] | None = None, fetchone_rows: list[object | None] | None = None
    ) -> None:
        self._rowcounts = rowcounts or [1]
        self._fetchone_rows = fetchone_rows or [(1,)]
        self.rowcount = 0

    def execute(self, *_: object, **__: object) -> None:
        self.rowcount = self._rowcounts.pop(0) if self._rowcounts else 1
        return

    def fetchone(self) -> object | None:
        return self._fetchone_rows.pop(0) if self._fetchone_rows else (1,)

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class _FakeConn:
    def __init__(
        self, rowcounts: list[int] | None = None, fetchone_rows: list[object | None] | None = None
    ) -> None:
        self._rowcounts = rowcounts
        self._fetchone_rows = fetchone_rows

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rowcounts, self._fetchone_rows)

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


@contextmanager
def _fake_connection(
    rowcounts: list[int] | None = None, fetchone_rows: list[object | None] | None = None
) -> _FakeConn:
    yield _FakeConn(rowcounts=rowcounts, fetchone_rows=fetchone_rows)


def _offer(
    *, line: float = 25.5, over_price: int = -110, under_price: int = -110, explicit: bool = True
) -> PropOffer:
    return PropOffer(
        bookmaker_key="draftkings",
        bookmaker_event_id="ev1",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
        raw_player_name="Cade Cunningham",
        market="player_points",
        line=line,
        over_price=over_price,
        under_price=under_price,
        last_update=None,
        scraped_at=datetime(2026, 5, 9, 22, 0, tzinfo=timezone.utc),
        explicit_over_under=explicit,
    )


def test_loader_tracks_unresolved_event(monkeypatch: object) -> None:
    monkeypatch.setattr("data_pipeline.loaders.odds.get_connection", lambda: _fake_connection())
    monkeypatch.setattr("data_pipeline.loaders.odds.resolve_event_to_game_id", lambda **_: None)
    loader = OddsLoader()
    summary = loader.load_snapshot([_offer()], bookmaker_key="draftkings", market="player_points")
    assert summary.unresolved_events == 1
    assert summary.inserted == 0


def test_loader_inserts_when_resolved(monkeypatch: object) -> None:
    class _Resolved:
        game_id = "game-1"

    monkeypatch.setattr("data_pipeline.loaders.odds.get_connection", lambda: _fake_connection())
    monkeypatch.setattr("data_pipeline.loaders.odds.resolve_event_to_game_id", lambda **_: _Resolved())
    monkeypatch.setattr("data_pipeline.loaders.odds.resolve_player_name", lambda *_args, **_kwargs: "player-1")
    loader = OddsLoader()
    summary = loader.load_snapshot([_offer()], bookmaker_key="draftkings", market="player_points")
    assert summary.inserted == 1


def test_loader_idempotent_deduped(monkeypatch: object) -> None:
    class _Resolved:
        game_id = "game-1"

    # First execute for prop upsert returns 0 rows -> deduped path.
    monkeypatch.setattr(
        "data_pipeline.loaders.odds.get_connection",
        lambda: _fake_connection(rowcounts=[1, 0, 1]),
    )
    monkeypatch.setattr("data_pipeline.loaders.odds.resolve_event_to_game_id", lambda **_: _Resolved())
    monkeypatch.setattr("data_pipeline.loaders.odds.resolve_player_name", lambda *_args, **_kwargs: "player-1")
    summary = OddsLoader().load_snapshot([_offer()], bookmaker_key="draftkings", market="player_points")
    assert summary.inserted == 0
    assert summary.deduped == 1


def test_loader_skips_non_positive_line_before_upsert(monkeypatch: object) -> None:
    monkeypatch.setattr("data_pipeline.loaders.odds.get_connection", lambda: _fake_connection())
    summary = OddsLoader().load_snapshot(
        [_offer(line=0.0)], bookmaker_key="draftkings", market="player_points"
    )
    assert summary.inserted == 0
    assert summary.deduped == 0


def test_loader_skips_equal_prices_without_explicit_sides(monkeypatch: object) -> None:
    monkeypatch.setattr("data_pipeline.loaders.odds.get_connection", lambda: _fake_connection())
    summary = OddsLoader().load_snapshot(
        [_offer(over_price=2800, under_price=2800, explicit=False)],
        bookmaker_key="draftkings",
        market="player_points",
    )
    assert summary.inserted == 0
    assert summary.deduped == 0


def test_loader_skips_player_not_on_resolved_game_teams(monkeypatch: object) -> None:
    class _Resolved:
        game_id = "game-1"

    monkeypatch.setattr(
        "data_pipeline.loaders.odds.get_connection",
        lambda: _fake_connection(rowcounts=[1, 1], fetchone_rows=[None]),
    )
    monkeypatch.setattr("data_pipeline.loaders.odds.resolve_event_to_game_id", lambda **_: _Resolved())
    monkeypatch.setattr("data_pipeline.loaders.odds.resolve_player_name", lambda *_args, **_kwargs: "player-1")
    summary = OddsLoader().load_snapshot([_offer()], bookmaker_key="draftkings", market="player_points")
    assert summary.inserted == 0
    assert summary.deduped == 0
