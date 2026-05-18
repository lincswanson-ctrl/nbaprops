"""Unit tests for vig math helpers."""

from __future__ import annotations

import pytest

from betting.vig import (
    american_to_implied_prob,
    implied_prob_to_american,
    vig_remove_power,
    vig_remove_proportional,
)


def test_american_to_implied_prob() -> None:
    assert american_to_implied_prob(-110) == pytest.approx(0.5238, abs=1e-4)
    assert american_to_implied_prob(+100) == pytest.approx(0.5, abs=1e-4)
    assert american_to_implied_prob(+150) == pytest.approx(0.4, abs=1e-4)
    assert american_to_implied_prob(-200) == pytest.approx(0.6667, abs=1e-4)


def test_implied_prob_roundtrip() -> None:
    for american in (-200, -125, -110, +145, +250):
        prob = american_to_implied_prob(american)
        rebuilt = implied_prob_to_american(prob)
        assert rebuilt == pytest.approx(american, abs=1)
    # +100 and -100 both imply 50%; canonical integer odds from prob use -100.
    prob_half = american_to_implied_prob(+100)
    assert implied_prob_to_american(prob_half) == -100


def test_vig_remove_proportional_balanced() -> None:
    over, under = vig_remove_proportional(-110, -110)
    assert over == pytest.approx(0.5, abs=1e-6)
    assert under == pytest.approx(0.5, abs=1e-6)


def test_vig_remove_proportional_unbalanced() -> None:
    over, under = vig_remove_proportional(-150, +130)
    assert over + under == pytest.approx(1.0, abs=1e-9)
    assert over > under


def test_vig_remove_power_balanced() -> None:
    over, under = vig_remove_power(-110, -110)
    assert over == pytest.approx(0.5, abs=1e-6)
    assert under == pytest.approx(0.5, abs=1e-6)


def test_vig_remove_power_extreme() -> None:
    over, under = vig_remove_power(-500, +350)
    assert over + under == pytest.approx(1.0, abs=1e-9)
    assert over > under
