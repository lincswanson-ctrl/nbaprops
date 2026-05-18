"""Unit tests for common odds normalization helpers."""

from __future__ import annotations

import pytest

from data_pipeline.scrapers.odds.base import (
    canonical_market_names,
    normalize_market_name,
    normalize_team_abbr,
)


def test_canonical_market_names_count() -> None:
    assert len(canonical_market_names()) == 12


def test_market_normalization_aliases() -> None:
    assert normalize_market_name("Points") == "player_points"
    assert normalize_market_name("Pts + Reb + Ast") == "player_points_rebounds_assists"
    assert normalize_market_name("Stl + Blk") == "player_steals_blocks"


def test_team_name_normalization() -> None:
    assert normalize_team_abbr("LA Lakers") == "LAL"
    assert normalize_team_abbr("Los Angeles Lakers") == "LAL"
    assert normalize_team_abbr("Lakers") == "LAL"
    assert normalize_team_abbr("CLE Cavaliers") == "CLE"
    assert normalize_team_abbr("DET Pistons") == "DET"


def test_team_name_normalization_unknown_raises() -> None:
    with pytest.raises(ValueError):
        normalize_team_abbr("Seattle SuperSonics")
