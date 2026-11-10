"""Unit tests for betting.grading (spec 07)."""

from __future__ import annotations

import pytest

from betting.grading import grade_bet, profit_units


@pytest.mark.parametrize(
    ("side", "line", "actual", "expected"),
    [
        ("over", 24.5, 25, "win"),
        ("over", 24.5, 24, "loss"),
        ("under", 24.5, 24, "win"),
        ("under", 24.5, 30, "loss"),
        ("over", 20.0, 20, "push"),
        ("under", 20.0, 20, "push"),
        ("over", 20.5, None, "void"),
    ],
)
def test_grade_bet(side: str, line: float, actual: float | None, expected: str) -> None:
    assert grade_bet(side, line, actual) == expected  # type: ignore[arg-type]


def test_profit_units_win_negative_and_positive_prices() -> None:
    assert profit_units("win", -110) == pytest.approx(100 / 110)
    assert profit_units("win", 150) == pytest.approx(1.5)


def test_profit_units_loss_push_void() -> None:
    assert profit_units("loss", -110) == -1.0
    assert profit_units("push", -110) == 0.0
    assert profit_units("void", 150) == 0.0


def test_profit_units_rejects_invalid_price() -> None:
    with pytest.raises(ValueError):
        profit_units("win", 50)
