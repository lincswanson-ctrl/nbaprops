"""Unit tests for betting.blend (spec 08)."""

from __future__ import annotations

import random

import pytest

from betting.blend import BlendSample, blend_prob, fit_weight, weight_for_market


def test_blend_prob_endpoints_and_midpoint() -> None:
    assert blend_prob(0.7, 0.5, 1.0) == pytest.approx(0.7)
    assert blend_prob(0.7, 0.5, 0.0) == pytest.approx(0.5)
    assert 0.5 < blend_prob(0.7, 0.5, 0.5) < 0.7


def test_blend_prob_handles_extremes_and_rejects_bad_weight() -> None:
    assert 0.0 < blend_prob(0.0, 0.5, 0.5) < 0.5
    with pytest.raises(ValueError):
        blend_prob(0.6, 0.5, 1.5)


def _samples(model_informative: bool, n: int = 4000) -> list[BlendSample]:
    rng = random.Random(1)
    out = []
    for _ in range(n):
        truth = rng.uniform(0.2, 0.8)
        p_model = truth if model_informative else rng.uniform(0.2, 0.8)
        out.append(BlendSample(p_model=p_model, p_market=0.5, outcome=int(rng.random() < truth)))
    return out


def test_fit_weight_trusts_informative_model() -> None:
    fit = fit_weight(_samples(model_informative=True))
    assert fit.weight > 0.8
    assert fit.log_loss_blend < fit.log_loss_market


def test_fit_weight_ignores_noise_model() -> None:
    fit = fit_weight(_samples(model_informative=False))
    assert fit.weight < 0.15
    assert fit.log_loss_blend <= fit.log_loss_model


def test_fit_weight_empty_raises() -> None:
    with pytest.raises(ValueError):
        fit_weight([])


def test_weight_for_market_falls_back_to_default() -> None:
    weights = {"default": 0.25, "player_points": 0.4}
    assert weight_for_market(weights, "player_points") == 0.4
    assert weight_for_market(weights, "player_assists") == 0.25
    with pytest.raises(KeyError):
        weight_for_market({}, "player_points")
