"""Unit tests for models.evaluation (spec 07)."""

from __future__ import annotations

import math

import pytest

from models.evaluation import brier_score, brier_skill_vs_base_rate, log_loss, reliability_bins


def test_brier_and_log_loss_perfect_and_coinflip() -> None:
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))


def test_log_loss_clips_certain_wrong_forecast() -> None:
    assert math.isfinite(log_loss([0.0], [1]))


def test_brier_skill_vs_base_rate() -> None:
    outcomes = [1, 0, 1, 0]
    assert brier_skill_vs_base_rate([0.5] * 4, outcomes) == pytest.approx(0.0)
    assert brier_skill_vs_base_rate([0.9, 0.1, 0.9, 0.1], outcomes) > 0
    assert brier_skill_vs_base_rate([0.1, 0.9, 0.1, 0.9], outcomes) < 0
    assert brier_skill_vs_base_rate([0.7, 0.7], [1, 1]) == 0.0


def test_reliability_bins_group_and_skip_empty() -> None:
    bins = reliability_bins([0.05, 0.08, 0.95, 1.0], [0, 1, 1, 1], n_bins=10)
    assert [(b.lower, b.count) for b in bins] == [(0.0, 2), (0.9, 2)]
    assert bins[0].observed_rate == 0.5


def test_metrics_reject_bad_input() -> None:
    with pytest.raises(ValueError):
        brier_score([], [])
    with pytest.raises(ValueError):
        log_loss([0.5], [1, 0])
    with pytest.raises(ValueError):
        reliability_bins([0.5], [1], n_bins=0)
