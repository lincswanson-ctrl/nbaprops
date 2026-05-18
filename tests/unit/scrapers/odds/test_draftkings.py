"""Unit tests for DraftKings odds parser utilities."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

from data_pipeline.scrapers.odds.base import OddsEvent, normalize_market_name
from data_pipeline.scrapers.odds.draftkings import (
    DraftKingsScraper,
    _extract_events_from_payload,
    _market_type_summary,
    _parse_draftkings_player_props,
)

FIXTURE = Path("tests/fixtures/odds/draftkings/player_points_sample.json")


def _event() -> OddsEvent:
    return OddsEvent(
        bookmaker_key="draftkings",
        bookmaker_event_id="dk_event_1",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
    )


def test_dk_parse_player_points_market() -> None:
    payload = cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))
    offers = _parse_draftkings_player_props(payload, _event(), "player_points")
    assert offers
    assert offers[0].raw_player_name == "Cade Cunningham"
    assert offers[0].line == 25.5
    assert offers[0].explicit_over_under


def test_dk_parse_handles_alt_lines() -> None:
    payload = cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))
    offers = _parse_draftkings_player_props(payload, _event(), "player_points")
    lines = sorted({offer.line for offer in offers})
    assert lines == [25.5, 27.5]


def test_market_normalization_points_aliases() -> None:
    assert normalize_market_name("Points") == "player_points"
    assert normalize_market_name("player_points") == "player_points"


def test_scraper_saves_raw_before_parsing_failure(tmp_path: Path) -> None:
    scraper = DraftKingsScraper()
    scraper._raw_root = tmp_path  # type: ignore[attr-defined]
    path = scraper._save_raw({"bad": "payload"}, "ev1", "player_points")  # type: ignore[attr-defined]
    assert path.exists()


def test_extract_events_from_payload_handles_events_dict() -> None:
    payload = {
        "events": {
            "123": {
                "eventId": "123",
                "teamName1": "Detroit Pistons",
                "teamName2": "Orlando Magic",
                "startDateTime": "2026-05-10T23:00:00Z",
            }
        }
    }
    events = _extract_events_from_payload(payload)
    assert len(events) == 1
    assert events[0].bookmaker_event_id == "123"
    assert events[0].away_team_name == "Detroit Pistons"
    assert events[0].home_team_name == "Orlando Magic"


def test_extract_events_from_payload_handles_nested_event_objects() -> None:
    payload = {
        "events": {
            "123": {
                "event": {
                    "id": "123",
                    "awayTeamName": "Detroit Pistons",
                    "homeTeamName": "Orlando Magic",
                    "eventStartDate": "2026-05-10T23:00:00Z",
                }
            }
        }
    }
    events = _extract_events_from_payload(payload)
    assert len(events) == 1
    assert events[0].bookmaker_event_id == "123"
    assert events[0].away_team_name == "Detroit Pistons"
    assert events[0].home_team_name == "Orlando Magic"


def test_extract_events_from_payload_handles_attachments_events() -> None:
    payload = {
        "attachments": {
            "events": {
                "123": {
                    "eventId": "123",
                    "teamName1": "Detroit Pistons",
                    "teamName2": "Orlando Magic",
                    "startDate": "2026-05-10T23:00:00Z",
                }
            }
        }
    }
    events = _extract_events_from_payload(payload)
    assert len(events) == 1
    assert events[0].bookmaker_event_id == "123"


def test_extract_events_from_payload_handles_eventgroup_dict() -> None:
    payload = {
        "eventGroup": {
            "events": {
                "123": {
                    "id": "123",
                    "awayTeamName": "Detroit Pistons",
                    "homeTeamName": "Orlando Magic",
                    "startDateTime": "2026-05-10T23:00:00Z",
                }
            }
        }
    }
    events = _extract_events_from_payload(payload)
    assert len(events) == 1
    assert events[0].bookmaker_event_id == "123"


def test_extract_events_from_payload_handles_start_event_date_with_7_fraction_digits() -> None:
    payload = {
        "events": [
            {
                "id": "34109299",
                "startEventDate": "2026-05-09T19:10:00.0000000Z",
                "participants": [
                    {"name": "CLE Cavaliers", "venueRole": "Home"},
                    {"name": "DET Pistons", "venueRole": "Away"},
                ],
            }
        ]
    }
    events = _extract_events_from_payload(payload)
    assert len(events) == 1
    assert events[0].bookmaker_event_id == "34109299"
    assert events[0].home_team_name == "CLE Cavaliers"
    assert events[0].away_team_name == "DET Pistons"
    assert events[0].commence_time == datetime(2026, 5, 9, 19, 10, tzinfo=timezone.utc)


def test_dk_parse_player_props_supports_fallback_fields() -> None:
    payload = {
        "selections": [
            {
                "name": "LeBron James",
                "selectionType": "Over",
                "line": "25.5",
                "americanOdds": "-115",
            },
            {
                "name": "LeBron James",
                "selectionType": "Under",
                "line": "25.5",
                "americanOdds": "-105",
            },
        ]
    }
    offers = _parse_draftkings_player_props(payload, _event(), "player_points")
    assert len(offers) == 1
    assert offers[0].raw_player_name == "LeBron James"
    assert offers[0].line == 25.5
    assert offers[0].over_price == -115
    assert offers[0].under_price == -105


def test_dk_parse_player_props_from_market_outcomes() -> None:
    payload = {
        "markets": [
            {
                "id": "m1",
                "eventId": "dk_event_1",
                "outcomes": [
                    {
                        "participantName": "Jayson Tatum",
                        "label": "Over 27.5",
                        "line": 27.5,
                        "americanOdds": -120,
                    },
                    {
                        "participantName": "Jayson Tatum",
                        "label": "Under 27.5",
                        "line": 27.5,
                        "americanOdds": 100,
                    },
                ],
            }
        ]
    }
    offers = _parse_draftkings_player_props(payload, _event(), "player_points")
    assert len(offers) == 1
    assert offers[0].raw_player_name == "Jayson Tatum"
    assert offers[0].line == 27.5
    assert offers[0].over_price == -120
    assert offers[0].under_price == 100


def test_dk_parse_player_props_with_ou_tokens_and_nested_odds() -> None:
    payload = {
        "markets": [{"id": "m2", "eventId": "dk_event_1", "line": 24.5}],
        "selections": [
            {"participantName": "Donovan Mitchell", "selectionType": "O", "marketId": "m2", "odds": {"american": -110}},
            {"participantName": "Donovan Mitchell", "selectionType": "U", "marketId": "m2", "odds": {"american": -110}},
        ],
    }
    offers = _parse_draftkings_player_props(payload, _event(), "player_points")
    assert len(offers) == 1
    assert offers[0].raw_player_name == "Donovan Mitchell"
    assert offers[0].line == 24.5
    assert offers[0].over_price == -110
    assert offers[0].under_price == -110


def test_dk_parse_player_props_ignores_one_sided_milestones() -> None:
    payload = {
        "markets": [{"id": "m3", "eventId": "dk_event_1", "name": "LeBron James Points"}],
        "selections": [
            {
                "label": "25+",
                "milestoneValue": 25,
                "marketId": "m3",
                "participants": [{"name": "LeBron James"}],
                "displayOdds": {"american": "-130"},
            }
        ],
    }
    offers = _parse_draftkings_player_props(payload, _event(), "player_points")
    assert offers == []


def test_dk_market_type_summary_counts_market_types() -> None:
    payload = {
        "markets": [
            {"marketType": {"id": "12189", "name": "Points Milestones"}},
            {"marketType": {"id": "12189", "name": "Points Milestones"}},
            {"marketType": {"id": "777", "name": "Player Points"}},
        ]
    }
    summary = _market_type_summary(payload)
    assert summary == [
        {"id": "12189", "name": "Points Milestones", "count": 2},
        {"id": "777", "name": "Player Points", "count": 1},
    ]


def test_dk_list_events_probes_configured_player_points_subcategory(monkeypatch: object) -> None:
    class _Settings:
        draftkings_player_points_subcategory_ids = "77777,16477"

    class _Scraper(DraftKingsScraper):
        def __init__(self) -> None:
            self.seen: list[str | None] = []

        async def _fetch_market_payload(self, *, subcategory_id: str | None = None) -> dict[str, Any]:
            self.seen.append(subcategory_id)
            return {"events": []}

        async def _fetch_market_payload_once(self) -> dict[str, Any]:
            return {"events": []}

        async def _fetch_eventgroup_payload(self) -> dict[str, Any]:
            return {"eventGroup": {"events": []}}

    monkeypatch.setattr("data_pipeline.scrapers.odds.draftkings.get_settings", lambda: _Settings())
    scraper = _Scraper()
    asyncio.run(scraper.list_events())
    assert "77777" in scraper.seen


def test_dk_milestone_fallback_ignores_other_event_markets() -> None:
    payload = {
        "markets": [{"id": "m-other", "eventId": "dk_event_2", "name": "LeBron James Points"}],
        "selections": [
            {
                "label": "20+",
                "milestoneValue": 20,
                "marketId": "m-other",
                "participants": [{"name": "LeBron James"}],
                "displayOdds": {"american": "+2800"},
            }
        ],
    }
    offers = _parse_draftkings_player_props(payload, _event(), "player_points")
    assert offers == []


def test_dk_classic_parse_ignores_other_event_market_outcomes() -> None:
    payload = {
        "markets": [
            {
                "id": "m-other",
                "eventId": "dk_event_2",
                "outcomes": [
                    {
                        "participantName": "LeBron James",
                        "label": "Over 19.5",
                        "line": 19.5,
                        "americanOdds": 2800,
                    },
                    {
                        "participantName": "LeBron James",
                        "label": "Under 19.5",
                        "line": 19.5,
                        "americanOdds": -20000,
                    },
                ],
            }
        ]
    }
    offers = _parse_draftkings_player_props(payload, _event(), "player_points")
    assert offers == []


def test_dk_combo_event_subcat_id_can_be_overridden_by_env(monkeypatch: object) -> None:
    """Combo markets should be enabled via settings without hardcoding IDs."""

    class _Settings:
        raw_storage_path = Path("./data/raw")
        draftkings_player_points_subcategory_ids = ""
        draftkings_event_subcat_player_points_rebounds_assists = "99999"
        draftkings_event_subcat_player_points_rebounds = ""
        draftkings_event_subcat_player_points_assists = ""
        draftkings_event_subcat_player_rebounds_assists = ""

    monkeypatch.setattr("data_pipeline.scrapers.odds.draftkings.get_settings", lambda: _Settings())

    async def _run() -> tuple[str, int]:
        async with DraftKingsScraper() as scraper:
            scraper._fetch_event_subcat_payload = AsyncMock(  # type: ignore[method-assign]
                return_value={
                    "markets": [
                        {
                            "id": "m1",
                            "eventId": "event123",
                            "outcomes": [
                                {
                                    "participantName": "Cade Cunningham",
                                    "label": "Over 20.5",
                                    "line": 20.5,
                                    "americanOdds": -110,
                                },
                                {
                                    "participantName": "Cade Cunningham",
                                    "label": "Under 20.5",
                                    "line": 20.5,
                                    "americanOdds": -110,
                                },
                            ],
                        }
                    ]
                }
            )
            event = OddsEvent(
                bookmaker_key="draftkings",
                bookmaker_event_id="event123",
                home_team_name="Detroit Pistons",
                away_team_name="Cleveland Cavaliers",
                commence_time=datetime(2026, 5, 14, 0, 10, tzinfo=timezone.utc),
            )
            offers = await scraper.fetch_player_props(event, "player_points_rebounds_assists")
            called_sid = scraper._fetch_event_subcat_payload.call_args.args[1]  # type: ignore[union-attr]
            return str(called_sid), len(offers)

    sid, offer_count = asyncio.run(_run())
    assert sid == "99999"
    assert offer_count == 1
