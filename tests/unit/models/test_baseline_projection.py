"""Unit tests for the baseline_v1 projection model (spec 05)."""

from __future__ import annotations

import math

import pytest

from models.baseline.projection import (
    BASELINE_MAX_GAMES,
    MIN_SIGMA,
    PROB_CEIL,
    PROB_FLOOR,
    Projection,
    market_stat_values,
    prob_over,
    project,
    recency_weights,
    score_market,
)

# --- market_stat_values -------------------------------------------------------------------


def test_market_stat_values_single_stat() -> None:
    games = [{"points": 20, "rebounds": 5}, {"points": 10, "rebounds": 7}]
    assert market_stat_values("player_points", games) == [20.0, 10.0]


def test_market_stat_values_combo_sums_components() -> None:
    games = [{"points": 20, "rebounds": 5, "assists": 3}]
    assert market_stat_values("player_points_rebounds_assists", games) == [28.0]
    assert market_stat_values("player_steals_blocks", [{"steals": 1, "blocks": 2}]) == [3.0]


def test_market_stat_values_skips_games_missing_a_component() -> None:
    games = [{"points": 20, "rebounds": None}, {"points": 12, "rebounds": 4}]
    assert market_stat_values("player_points_rebounds", games) == [16.0]


def test_market_stat_values_unknown_market() -> None:
    assert market_stat_values("player_double_double", [{"points": 1}]) is None


# --- recency_weights ----------------------------------------------------------------------


def test_recency_weights_half_life() -> None:
    weights = recency_weights(9, half_life=8)
    assert weights[0] == 1.0
    assert weights[8] == pytest.approx(0.5)


def test_recency_weights_empty_and_invalid() -> None:
    assert recency_weights(0) == []
    with pytest.raises(ValueError):
        recency_weights(3, half_life=0)


# --- project ------------------------------------------------------------------------------


def test_project_weights_recent_games_more() -> None:
    proj = project([30, 30, 30, 10, 10, 10, 10, 10])
    assert proj is not None
    assert proj.mean > 17.5  # unweighted mean is 17.5; recent 30s pull it up


def test_project_variance_floored_at_mean() -> None:
    proj = project([12, 12, 12, 12, 12])
    assert proj is not None
    assert proj.mean == pytest.approx(12)
    assert proj.sigma == pytest.approx(math.sqrt(12))


def test_project_sigma_floor_for_zero_stats() -> None:
    proj = project([0, 0, 0, 0, 0])
    assert proj is not None
    assert proj.sigma == MIN_SIGMA


def test_project_too_few_games_returns_none() -> None:
    assert project([20, 22, 18, 25]) is None
    assert project([]) is None


def test_project_caps_history_at_max_games() -> None:
    proj = project([10.0] * BASELINE_MAX_GAMES + [100.0] * 10)
    assert proj is not None
    assert proj.games_used == BASELINE_MAX_GAMES
    assert proj.mean == pytest.approx(10)


# --- prob_over ----------------------------------------------------------------------------


def test_prob_over_line_at_mean_is_near_half() -> None:
    assert prob_over(24.5, Projection(mean=24.5, sigma=6.0, games_used=10)) == pytest.approx(
        0.5, abs=1e-9
    )


def test_prob_over_is_symmetric_around_mean() -> None:
    proj = Projection(mean=24.0, sigma=6.0, games_used=10)
    assert prob_over(28.5, proj) < 0.5 < prob_over(19.5, proj)
    assert prob_over(28.5, proj) + prob_over(19.5, proj) == pytest.approx(1.0)


def test_prob_over_whole_line_excludes_push() -> None:
    proj = Projection(mean=20.0, sigma=5.0, games_used=10)
    # Line exactly at the mean: over and under are equally likely once pushes are removed.
    assert prob_over(20, proj) == pytest.approx(0.5)
    assert prob_over(22, proj) < 0.5


def test_prob_over_clamped() -> None:
    proj = Projection(mean=30.0, sigma=1.0, games_used=10)
    assert prob_over(5.5, proj) == PROB_CEIL
    assert prob_over(60.5, proj) == PROB_FLOOR


# --- score_market -------------------------------------------------------------------------


def test_score_market_line_above_average_favors_under() -> None:
    games = [{"points": p} for p in [22, 25, 24, 26, 23, 24, 25, 23]]
    prob = score_market("player_points", 28.5, games)
    assert prob is not None and prob < 0.5


def test_score_market_differs_by_market_for_same_player() -> None:
    games = [{"points": 25, "rebounds": 4, "assists": 9} for _ in range(8)]
    pts = score_market("player_points", 22.5, games)
    reb = score_market("player_rebounds", 6.5, games)
    assert pts is not None and reb is not None
    assert pts > 0.5 > reb


def test_score_market_missing_data_returns_none() -> None:
    assert score_market("player_points", 20.5, [{"points": 20}] * 3) is None
    assert score_market("player_unknown", 20.5, [{"points": 20}] * 10) is None
