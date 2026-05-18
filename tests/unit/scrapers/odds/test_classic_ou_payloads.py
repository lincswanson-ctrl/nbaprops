"""Integration-style checks that saved odds payloads stay classic O/U only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from data_pipeline.scrapers.odds.base import OddsEvent
from data_pipeline.scrapers.odds.draftkings import _parse_draftkings_player_props
from data_pipeline.scrapers.odds.fanduel import (
    _merge_fanduel_nav_and_prices,
    _parse_fanduel_attachment_markets,
)


def _dk_event() -> OddsEvent:
    return OddsEvent(
        bookmaker_key="draftkings",
        bookmaker_event_id="dk_event_1",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
    )


def _fd_event() -> OddsEvent:
    return OddsEvent(
        bookmaker_key="fanduel",
        bookmaker_event_id="fd_event_1",
        home_team_name="Orlando Magic",
        away_team_name="Detroit Pistons",
        commence_time=datetime(2026, 5, 9, 23, 0, tzinfo=timezone.utc),
    )


def test_saved_payloads_keep_classic_ou_only() -> None:
    """Fail fast if milestone ladders parse into PropOffers or classic payloads vanish."""

    root = Path("tests/fixtures/odds")
    dk_classic = cast(
        dict[str, Any], json.loads((root / "draftkings/player_points_classic_ou.json").read_text(encoding="utf-8"))
    )
    dk_ms = cast(
        dict[str, Any],
        json.loads((root / "draftkings/player_points_milestones_only.json").read_text(encoding="utf-8")),
    )
    fd_classic = cast(
        dict[str, Any], json.loads((root / "fanduel/player_points_classic_ou.json").read_text(encoding="utf-8"))
    )
    fd_ladder = cast(
        dict[str, Any], json.loads((root / "fanduel/player_points_ladder_only.json").read_text(encoding="utf-8"))
    )

    dk_ok = _parse_draftkings_player_props(dk_classic, _dk_event(), "player_points")
    assert dk_ok, "classic DK fixture must yield at least one PropOffer"
    assert _parse_draftkings_player_props(dk_ms, _dk_event(), "player_points") == []

    fd_ok = _parse_fanduel_attachment_markets(fd_classic["markets"], _fd_event(), "player_points")
    assert fd_ok, "classic FD fixture must yield at least one PropOffer"
    assert _parse_fanduel_attachment_markets(fd_ladder["markets"], _fd_event(), "player_points") == []

    overlay = _merge_fanduel_nav_and_prices(fd_classic["markets"], [{"marketId": 902, "note": "priced"}])
    assert overlay[0].get("note") == "priced"


def test_classic_fixture_prices_are_distinct_pairs() -> None:
    """Classic ladder spam should not synthesize identical fabricated unders."""

    dk_classic = cast(
        dict[str, Any],
        json.loads(Path("tests/fixtures/odds/draftkings/player_points_classic_ou.json").read_text(encoding="utf-8")),
    )
    offers = _parse_draftkings_player_props(dk_classic, _dk_event(), "player_points")
    assert len(offers) == 1
    assert offers[0].over_price == -105
    assert offers[0].under_price == -115
