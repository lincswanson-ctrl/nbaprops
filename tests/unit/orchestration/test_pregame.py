"""Unit tests for orchestration.pregame helpers."""

from __future__ import annotations

from datetime import date

import pytest

from orchestration import pregame
from scripts.run_pregame import _resolve_date


def test_resolve_date_today(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeDate(date):
        @classmethod
        def today(cls) -> "_FakeDate":
            return cls(2026, 5, 9)

    monkeypatch.setattr("scripts.run_pregame.date", _FakeDate)
    assert _resolve_date("today") == date(2026, 5, 9)


def test_american_to_prob_handles_positive_and_negative() -> None:
    assert pregame._american_to_prob(-110) == pytest.approx(0.5238, rel=1e-3)
    assert pregame._american_to_prob(+120) == pytest.approx(0.4545, rel=1e-3)


def test_edge_confidence_buckets() -> None:
    assert pregame._edge_confidence(0.08) == "high"
    assert pregame._edge_confidence(0.05) == "medium"
    assert pregame._edge_confidence(0.02) == "low"


def test_edge_pct_computation() -> None:
    assert pregame._edge_pct(0.57, 0.52) == pytest.approx(0.05)


def test_extract_espn_event_pairs() -> None:
    payload = {
        "events": [
            {
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Los Angeles Lakers"}},
                            {"homeAway": "away", "team": {"displayName": "Boston Celtics"}},
                        ]
                    }
                ]
            }
        ]
    }
    assert pregame._extract_espn_event_pairs(payload) == [("Los Angeles Lakers", "Boston Celtics")]


def test_seed_game_values_contains_required_defaults() -> None:
    values = pregame._seed_game_values(
        game_id="espn_BOS_LAL_2026-05-10",
        target_date=date(2026, 5, 10),
        season="2025-26",
        home_team_id="1610612747",
        away_team_id="1610612738",
    )
    assert values["season_type"] == "Regular Season"
    assert values["season_year"] == 2025
    assert values["season_type"] == "Regular Season"


def test_seed_insert_candidates_include_season_type_variants() -> None:
    values = {"game_id": "g1", "season_type": "Regular Season"}
    candidates = pregame._seed_insert_candidates(values)
    season_types = {candidate["season_type"] for candidate in candidates}
    assert "Regular Season" in season_types
    assert "regular" in season_types
    assert "REG" in season_types


def test_run_odds_raises_when_no_odds_and_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pregame, "_require_pregame_schema", lambda: None)
    monkeypatch.setattr(pregame, "_scrape_player_prop_odds", lambda _d, _s: None)
    monkeypatch.setattr(
        pregame,
        "_build_odds_tables",
        lambda _d: {"games_count": 2, "markets_ingested": 0, "normalized_rows": 0},
    )
    monkeypatch.setattr(
        pregame,
        "_build_odds_tables_from_csv",
        lambda _d: {"games_count": 2, "markets_ingested": 0, "normalized_rows": 0, "csv_fallback_rows": 0},
    )

    with pytest.raises(RuntimeError, match="allow-empty-odds"):
        pregame.run_odds(date(2026, 5, 10), "2025-26", allow_empty_odds=False)


def test_run_odds_allows_empty_when_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pregame, "_require_pregame_schema", lambda: None)
    monkeypatch.setattr(pregame, "_scrape_player_prop_odds", lambda _d, _s: None)
    monkeypatch.setattr(
        pregame,
        "_build_odds_tables",
        lambda _d: {"games_count": 2, "markets_ingested": 0, "normalized_rows": 0},
    )
    monkeypatch.setattr(
        pregame,
        "_build_odds_tables_from_csv",
        lambda _d: {"games_count": 2, "markets_ingested": 0, "normalized_rows": 0, "csv_fallback_rows": 0},
    )

    result = pregame.run_odds(date(2026, 5, 10), "2025-26", allow_empty_odds=True)
    assert result.name == "2_odds"
    assert result.details["games_count"] == 2
    assert result.details["markets_ingested"] == 0


def test_scrape_player_prop_odds_does_not_refresh_today_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_run_once(
        book: str,
        markets: list[str],
        *,
        refresh_schedule: bool = True,
    ) -> int:
        captured["book"] = book
        captured["markets"] = markets
        captured["refresh_schedule"] = refresh_schedule
        return 0

    monkeypatch.setattr(pregame, "_refresh_target_schedule", lambda _d, _s: None)
    monkeypatch.setattr(pregame, "canonical_market_names", lambda: ["player_points"])
    monkeypatch.setattr(pregame, "run_odds_once", _fake_run_once)

    pregame._scrape_player_prop_odds(date(2026, 5, 10), "2025-26", books=("draftkings",))

    assert captured["book"] == "draftkings"
    assert captured["markets"] == ["player_points"]
    assert captured["refresh_schedule"] is False


def test_missing_pregame_schema_complete() -> None:
    columns = {t: set(cols) for t, cols in pregame.PREGAME_REQUIRED_COLUMNS.items()}
    assert pregame.missing_pregame_schema(columns, set(pregame.PREGAME_REQUIRED_UNIQUE_INDEXES)) == []


def test_missing_pregame_schema_reports_columns_tables_and_indexes() -> None:
    columns = {t: set(cols) for t, cols in pregame.PREGAME_REQUIRED_COLUMNS.items()}
    columns["odds"].discard("under_price")
    del columns["picks"]
    missing = pregame.missing_pregame_schema(columns, set())
    assert "odds.under_price" in missing
    assert "table picks" in missing
    assert "index ux_odds_normalized_game_player_market" in missing


def test_missing_pregame_schema_empty_database() -> None:
    missing = pregame.missing_pregame_schema({}, set())
    assert len(missing) == len(pregame.PREGAME_REQUIRED_COLUMNS) + len(
        pregame.PREGAME_REQUIRED_UNIQUE_INDEXES
    )
