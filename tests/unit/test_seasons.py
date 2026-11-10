"""Unit tests for NBA season label helpers."""

from __future__ import annotations

from datetime import date

import pytest

from data_pipeline.scrapers.odds.__main__ import infer_nba_season as odds_infer_nba_season
from data_pipeline.seasons import infer_nba_season, previous_season


@pytest.mark.parametrize(
    ("target_date", "expected"),
    [
        (date(2026, 11, 10), "2026-27"),  # typical: early regular season
        (date(2027, 3, 1), "2026-27"),  # typical: same season after new year
        (date(2026, 6, 30), "2025-26"),  # edge: last day before rollover (Finals window)
        (date(2026, 7, 1), "2026-27"),  # edge: first day of new season label
        (date(2099, 10, 1), "2099-00"),  # edge: century wrap
    ],
)
def test_infer_nba_season(target_date: date, expected: str) -> None:
    assert infer_nba_season(target_date) == expected


def test_odds_module_reexports_shared_helper() -> None:
    assert odds_infer_nba_season is infer_nba_season


@pytest.mark.parametrize(
    ("season", "expected"),
    [("2026-27", "2025-26"), ("2000-01", "1999-00"), ("2100-01", "2099-00")],
)
def test_previous_season(season: str, expected: str) -> None:
    assert previous_season(season) == expected


@pytest.mark.parametrize("bad", ["", "2026", "2026-28", "26-27", "2026-2027", "abcd-ef"])
def test_previous_season_rejects_invalid_labels(bad: str) -> None:
    with pytest.raises(ValueError):
        previous_season(bad)
