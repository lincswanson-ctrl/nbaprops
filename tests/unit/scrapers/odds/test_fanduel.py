"""Unit tests for FanDuel parser utilities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from data_pipeline.scrapers.odds.base import OddsEvent
from data_pipeline.scrapers.odds.fanduel import (
    _coerce_fanduel_price_payload,
    _fd_market_matches,
    _merge_fanduel_nav_and_prices,
    _parse_fanduel_attachment_markets,
    _parse_fanduel_player_props,
)

FIXTURE = Path("tests/fixtures/odds/fanduel/player_props_sample.json")


def _event() -> OddsEvent:
    return OddsEvent(
        bookmaker_key="fanduel",
        bookmaker_event_id="fd_event_1",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
    )


def test_fd_parse_player_props() -> None:
    payload = cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))
    offers = _parse_fanduel_player_props(payload, _event(), "player_points")
    assert len(offers) == 1
    offer = offers[0]
    assert offer.raw_player_name == "Cade Cunningham"
    assert offer.over_price == -105
    assert offer.under_price == -115


def test_fd_market_matches_player_points_excludes_to_score_ladders() -> None:
    assert not _fd_market_matches("player_points", {"marketName": "To Score 25+ Points"})
    assert _fd_market_matches("player_points", {"marketName": "Player Points"})


def test_fd_market_matches_threes_assists_rebounds_classic_only() -> None:
    assert _fd_market_matches("player_threes", {"marketName": "Player Made Threes"})
    assert not _fd_market_matches("player_threes", {"marketName": "4+ Made Threes"})
    assert _fd_market_matches("player_assists", {"marketName": "Player Assists"})
    assert not _fd_market_matches("player_assists", {"marketName": "To Record 6+ Assists"})
    assert _fd_market_matches("player_rebounds", {"marketName": "Player Rebounds"})
    assert not _fd_market_matches("player_rebounds", {"marketName": "To Record 8+ Rebounds"})


def test_fd_attachment_ignores_milestone_when_no_classic_ou() -> None:
    markets: list[dict[str, Any]] = [
        {
            "marketName": "To Score 20+ Points",
            "runners": [
                {
                    "runnerName": "Player B",
                    "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": -120}},
                }
            ],
        },
    ]
    offers = _parse_fanduel_attachment_markets(markets, _event(), "player_points")
    assert offers == []


def test_fd_parse_runner_name_over_under_shape() -> None:
    payload = {
        "markets": [
            {
                "marketName": "Player Points",
                "runners": [
                    {
                        "runnerName": "Over 12.5 Franz Wagner",
                        "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": -112}},
                    },
                    {
                        "runnerName": "Under 12.5 Franz Wagner",
                        "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": -108}},
                    },
                ],
            }
        ]
    }
    offers = _parse_fanduel_player_props(payload, _event(), "player_points")
    assert len(offers) == 1
    assert offers[0].raw_player_name == "Franz Wagner"
    assert offers[0].line == 12.5
    assert offers[0].over_price == -112
    assert offers[0].under_price == -108
    assert offers[0].explicit_over_under


def test_fd_market_prices_accepts_keyed_map_payload() -> None:
    payload = {"markets": {"901.1": {"marketId": "901.1", "runners": []}}}
    rows = _coerce_fanduel_price_payload(payload)
    assert rows == [{"marketId": "901.1", "runners": []}]


def test_fd_merge_prices_handles_composite_market_ids() -> None:
    nav_rows = [{"marketId": "901.1", "marketName": "Player Points"}]
    priced_rows = [{"marketId": 901, "runners": [{"runnerName": "Over 12.5 Franz Wagner"}]}]
    merged = _merge_fanduel_nav_and_prices(nav_rows, priced_rows)
    assert merged[0]["marketName"] == "Player Points"
    assert merged[0]["runners"] == [{"runnerName": "Over 12.5 Franz Wagner"}]
