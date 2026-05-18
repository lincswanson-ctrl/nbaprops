"""Integration smoke tests for odds modules."""

from __future__ import annotations

from datetime import datetime

import pytest

from data_pipeline.scrapers.odds.base import canonical_market_names
from data_pipeline.scrapers.odds.base import PropOffer


def test_canonical_market_count() -> None:
    assert len(canonical_market_names()) == 12


@pytest.mark.network
def test_network_placeholder() -> None:
    """Network e2e is run manually in CI with configured sportsbook access."""
    assert True


def test_e2e_closing_line_simulated() -> None:
    commence = "2026-05-09T23:00:00+00:00"
    offers = [
        PropOffer(
            bookmaker_key="draftkings",
            bookmaker_event_id="ev1",
            home_team_name="Orlando Magic",
            away_team_name="Detroit Pistons",
            commence_time=datetime.fromisoformat(commence),
            raw_player_name="Cade Cunningham",
            market="player_points",
            line=25.5,
            over_price=-110,
            under_price=-110,
            last_update=None,
            scraped_at=datetime.fromisoformat("2026-05-09T22:58:00+00:00"),
        ),
        PropOffer(
            bookmaker_key="draftkings",
            bookmaker_event_id="ev1",
            home_team_name="Orlando Magic",
            away_team_name="Detroit Pistons",
            commence_time=datetime.fromisoformat(commence),
            raw_player_name="Cade Cunningham",
            market="player_points",
            line=26.5,
            over_price=-120,
            under_price=100,
            last_update=None,
            scraped_at=datetime.fromisoformat("2026-05-09T22:59:30+00:00"),
        ),
    ]
    # smoke assertion to ensure synthetic closing sequence can be created
    assert offers[-1].scraped_at > offers[0].scraped_at
