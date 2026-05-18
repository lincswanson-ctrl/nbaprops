from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scripts.dk_discover_combo_event_subcats import (
    _count_player_ou_pairs,
    _match_combo_market,
    discover_combo_subcategories_for_event,
)


def test_match_combo_market() -> None:
    assert _match_combo_market("Player Points + Rebounds + Assists O/U") == "player_points_rebounds_assists"
    assert _match_combo_market("Player Points + Rebounds O/U") == "player_points_rebounds"
    assert _match_combo_market("Player Points + Assists O/U") == "player_points_assists"
    assert _match_combo_market("Player Rebounds + Assists O/U") == "player_rebounds_assists"
    assert _match_combo_market("Alt Team Total Points") is None


def test_count_player_ou_pairs_counts_only_real_pairs() -> None:
    payload: dict[str, Any] = {
        "selections": [
            {
                "participants": [{"name": "Cade Cunningham"}],
                "label": "Over 20.5",
                "points": 20.5,
                "displayOdds": {"american": "-110"},
            },
            {
                "participants": [{"name": "Cade Cunningham"}],
                "label": "Under 20.5",
                "points": 20.5,
                "displayOdds": {"american": "-110"},
            },
            # milestone ignored
            {
                "participants": [{"name": "Cade Cunningham"}],
                "label": "20+",
                "milestoneValue": 20,
                "points": 20,
            },
        ]
    }
    assert _count_player_ou_pairs(payload) == 1


def test_discover_combo_subcategories_for_event_handles_empty(monkeypatch: object) -> None:
    class _FakeDK:
        async def __aenter__(self) -> "_FakeDK":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def _fetch_event_subcat_payload(self, event_id: str, subcategory_id: str) -> dict[str, Any]:
            return {"markets": [], "selections": []}

    monkeypatch.setattr("scripts.dk_discover_combo_event_subcats.DraftKingsScraper", _FakeDK)
    found = asyncio.run(
        discover_combo_subcategories_for_event(
            event_id="123",
            min_id=100,
            max_id=110,
            step=1,
            concurrency=2,
        )
    )
    assert found == {}

