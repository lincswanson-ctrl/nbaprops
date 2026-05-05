"""Unit tests for the BetMGM odds scraper."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from data_pipeline.scrapers.odds.base import OddsEvent
from data_pipeline.scrapers.odds.betmgm import _parse_betmgm_player_props

PLAYER_PROPS_FIXTURE = Path("tests/fixtures/odds/betmgm/player_props_sample.json")
LIVE_POINTS_FIXTURE = Path("tests/fixtures/odds/betmgm/live_player_points_sample.json")
PLAYER_REBOUNDS_FIXTURE = Path("tests/fixtures/odds/betmgm/player_rebounds_sample.json")


def _build_betmgm_event() -> OddsEvent:
    return OddsEvent(
        bookmaker_key="betmgm",
        bookmaker_event_id="19411867",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 1, 23, 10, tzinfo=timezone.utc),
    )


def test_betmgm_parse_player_props() -> None:
    payload = cast(dict[str, Any], json.loads(PLAYER_PROPS_FIXTURE.read_text(encoding="utf-8")))
    offers = _parse_betmgm_player_props(payload, _build_betmgm_event(), "player_points_rebounds_assists")
    assert offers
    first = offers[0]
    assert first.raw_player_name == "Ausar Thompson (DET)"
    assert first.line > 0
    assert isinstance(first.over_price, int)
    assert isinstance(first.under_price, int)


def test_betmgm_parse_player_rebounds_market() -> None:
    payload = cast(dict[str, Any], json.loads(PLAYER_REBOUNDS_FIXTURE.read_text(encoding="utf-8")))
    offers = _parse_betmgm_player_props(payload, _build_betmgm_event(), "player_rebounds")
    assert len(offers) == 1
    assert offers[0].market == "player_rebounds"
    assert offers[0].raw_player_name == "Cade Cunningham"
    assert offers[0].line == 7.5
    assert offers[0].over_price == -115
    assert offers[0].under_price == -105


def test_betmgm_parse_wrong_market_returns_empty() -> None:
    """Parser returns nothing when market snippets do not match any optionMarket name."""
    payload = cast(dict[str, Any], json.loads(PLAYER_REBOUNDS_FIXTURE.read_text(encoding="utf-8")))
    offers = _parse_betmgm_player_props(payload, _build_betmgm_event(), "player_assists")
    assert offers == []


def test_betmgm_parse_live_player_points_props() -> None:
    payload = cast(dict[str, Any], json.loads(LIVE_POINTS_FIXTURE.read_text(encoding="utf-8")))
    offers = _parse_betmgm_player_props(payload, _build_betmgm_event(), "player_points")
    assert offers
    names = {offer.raw_player_name for offer in offers}
    assert "Cade Cunningham" in names


# ---------------------------------------------------------------------------
# Team moneyline tests
# ---------------------------------------------------------------------------

MONEYLINE_FIXTURE = Path("tests/fixtures/odds/betmgm/moneyline_sample.json")


def _load_moneyline_fixture() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MONEYLINE_FIXTURE.read_text(encoding="utf-8")))


def test_betmgm_player_prop_odds_still_parse_with_moneyline_fixture() -> None:
    """Player-prop parser is unaffected when fixture contains only game-line markets."""
    payload = _load_moneyline_fixture()
    offers = _parse_betmgm_player_props(payload, _build_betmgm_event(), "player_points")
    # Moneyline fixture has no optionMarkets → parser returns empty, not an error.
    assert offers == []


def test_betmgm_parse_moneyline_both_sides() -> None:
    """Moneyline parser extracts both team sides from a fixture-view payload."""
    from data_pipeline.scrapers.odds.betmgm import _parse_betmgm_moneyline_offers

    payload = _load_moneyline_fixture()
    event = OddsEvent(
        bookmaker_key="betmgm",
        bookmaker_event_id="123456",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 1, 23, 10, tzinfo=timezone.utc),
    )
    offers = _parse_betmgm_moneyline_offers(payload, event, None)

    assert len(offers) == 2
    teams = {o.team for o in offers}
    assert "ORL" in teams
    assert "DET" in teams


def test_betmgm_moneyline_odds_values_correct() -> None:
    """Moneyline parser maps americanOdds correctly to each team side."""
    from data_pipeline.scrapers.odds.betmgm import _parse_betmgm_moneyline_offers

    payload = _load_moneyline_fixture()
    event = OddsEvent(
        bookmaker_key="betmgm",
        bookmaker_event_id="123456",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 1, 23, 10, tzinfo=timezone.utc),
    )
    offers = _parse_betmgm_moneyline_offers(payload, event, None)
    by_team = {o.team: o for o in offers}

    assert by_team["DET"].moneyline_odds == 125
    assert by_team["ORL"].moneyline_odds == -145


def test_betmgm_moneyline_missing_games_section_returns_empty() -> None:
    """Moneyline parser returns empty list when fixture has no games section."""
    from data_pipeline.scrapers.odds.betmgm import _parse_betmgm_moneyline_offers

    payload: dict[str, Any] = {"fixture": {"id": "999", "participants": [], "games": []}}
    event = _build_betmgm_event()
    offers = _parse_betmgm_moneyline_offers(payload, event, None)
    assert offers == []


def test_betmgm_moneyline_money_line_two_words_matched() -> None:
    """Moneyline parser handles 'Money Line' (two words) as well as 'Moneyline'."""
    from data_pipeline.scrapers.odds.betmgm import _parse_betmgm_moneyline_offers

    payload: dict[str, Any] = {
        "fixture": {
            "id": "999",
            "participants": [
                {"participantId": "p1", "name": {"value": "Detroit Pistons"}},
                {"participantId": "p2", "name": {"value": "Orlando Magic"}},
            ],
            "games": [
                {
                    "name": {"value": "Money Line"},
                    "results": [
                        {"playerId": "p1", "americanOdds": 125},
                        {"playerId": "p2", "americanOdds": -145},
                    ],
                }
            ],
        }
    }
    event = OddsEvent(
        bookmaker_key="betmgm",
        bookmaker_event_id="999",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 5, 23, 0, tzinfo=timezone.utc),
    )
    offers = _parse_betmgm_moneyline_offers(payload, event, None)
    assert len(offers) == 2
    teams = {o.team for o in offers}
    assert "ORL" in teams
    assert "DET" in teams


# ---------------------------------------------------------------------------
# BetMGM list_events / competition filter tests
# ---------------------------------------------------------------------------


def _build_betmgm_listing_fixture(competition_name: str, teams: tuple[str, str]) -> dict[str, Any]:
    """Build a minimal BetMGM fixture-listing row for use in _parse_betmgm_events tests."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat() + "T20:00:00Z"
    return {
        "id": "999",
        "startDate": today,
        "competition": {"name": {"value": competition_name}},
        "participants": [
            {"name": {"value": teams[0]}},
            {"name": {"value": teams[1]}},
        ],
    }


def test_betmgm_parse_events_filters_non_nba_competitions() -> None:
    """_parse_betmgm_events skips fixtures whose competition is not NBA."""
    from data_pipeline.scrapers.odds.betmgm import _parse_betmgm_events

    payload = {
        "fixtures": [
            _build_betmgm_listing_fixture("NBA", ("Detroit Pistons", "Orlando Magic")),
            _build_betmgm_listing_fixture("BSL", ("Petkim spor Aliaga", "Besiktas")),
            _build_betmgm_listing_fixture("EuroLeague", ("Real Madrid", "CSKA Moscow")),
        ]
    }
    events = _parse_betmgm_events(payload)
    assert len(events) == 1
    assert events[0].home_team_name == "Orlando Magic"


def test_betmgm_parse_events_keeps_fixture_without_competition_metadata() -> None:
    """_parse_betmgm_events keeps fixtures with no competition key (fail-open)."""
    from data_pipeline.scrapers.odds.betmgm import _parse_betmgm_events
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat() + "T20:00:00Z"
    payload = {
        "fixtures": [
            {
                "id": "111",
                "startDate": today,
                # no "competition" key → kept so we don't silently drop NBA games
                "participants": [
                    {"name": {"value": "Detroit Pistons"}},
                    {"name": {"value": "Orlando Magic"}},
                ],
            }
        ]
    }
    events = _parse_betmgm_events(payload)
    assert len(events) == 1
