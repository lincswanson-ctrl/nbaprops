"""Tests for team id normalization used by NBA load validation."""

from __future__ import annotations

from decimal import Decimal

from data_pipeline.team_ids import normalize_team_id


def test_normalize_team_id_int_matches_string_nba_id() -> None:
    tid = 1610612747
    assert normalize_team_id(tid) == normalize_team_id(str(tid))


def test_normalize_team_id_decimal() -> None:
    assert normalize_team_id(Decimal("1610612747")) == "1610612747"
