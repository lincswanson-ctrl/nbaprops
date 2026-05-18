"""Unit tests for bookmaker event resolution."""

from __future__ import annotations

from datetime import datetime, timezone

from data_pipeline.loaders.event_resolution import ResolvedEvent, resolve_event_to_game_id


def test_event_resolution_returns_cached_mapping(monkeypatch: object) -> None:
    cached = ResolvedEvent(game_id="g1", home_team_id="161", away_team_id="162")

    def fake_cached(**_: object) -> ResolvedEvent:
        return cached

    monkeypatch.setattr("data_pipeline.loaders.event_resolution._read_cached_mapping", fake_cached)
    result = resolve_event_to_game_id(
        bookmaker_key="draftkings",
        bookmaker_event_id="ev1",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
    )
    assert result == cached


def test_event_resolution_unknown_team_returns_none(monkeypatch: object) -> None:
    monkeypatch.setattr("data_pipeline.loaders.event_resolution._read_cached_mapping", lambda **_: None)
    result = resolve_event_to_game_id(
        bookmaker_key="draftkings",
        bookmaker_event_id="ev1",
        home_team_name="Unknown Team",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
    )
    assert result is None


def test_event_resolution_la_clippers_lakers_disambiguation(monkeypatch: object) -> None:
    monkeypatch.setattr("data_pipeline.loaders.event_resolution._read_cached_mapping", lambda **_: None)
    monkeypatch.setattr(
        "data_pipeline.loaders.event_resolution._query_game",
        lambda **_: ResolvedEvent(game_id="g-la", home_team_id="lac", away_team_id="lal"),
    )
    cached: dict[str, object] = {}

    def _capture_cache(**kwargs: object) -> None:
        cached.update(kwargs)

    monkeypatch.setattr("data_pipeline.loaders.event_resolution._cache_mapping", _capture_cache)
    result = resolve_event_to_game_id(
        bookmaker_key="draftkings",
        bookmaker_event_id="ev-la",
        home_team_name="LA Clippers",
        away_team_name="LA Lakers",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
    )
    assert result is not None
    assert result.game_id == "g-la"
    assert cached["bookmaker_event_id"] == "ev-la"


def test_event_resolution_uses_synthetic_game_when_enabled(monkeypatch: object) -> None:
    monkeypatch.setattr("data_pipeline.loaders.event_resolution._read_cached_mapping", lambda **_: None)
    monkeypatch.setattr("data_pipeline.loaders.event_resolution._query_game", lambda **_: None)
    monkeypatch.setattr(
        "data_pipeline.loaders.event_resolution.get_settings",
        lambda: type("S", (), {"odds_allow_synthetic_games": True})(),
    )
    synthetic = ResolvedEvent(game_id="draftkings_ev1", home_team_id="1610612765", away_team_id="1610612753")
    monkeypatch.setattr(
        "data_pipeline.loaders.event_resolution._upsert_synthetic_game_and_mapping",
        lambda **_: synthetic,
    )

    result = resolve_event_to_game_id(
        bookmaker_key="draftkings",
        bookmaker_event_id="ev1",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
    )
    assert result == synthetic
