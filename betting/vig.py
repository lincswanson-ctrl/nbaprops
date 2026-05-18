"""Vig conversion and removal math helpers."""

from __future__ import annotations


def american_to_implied_prob(american_odds: int) -> float:
    """Convert American odds to implied probability."""

    if american_odds == 0:
        raise ValueError("American odds cannot be zero.")
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    abs_odds = float(abs(american_odds))
    return abs_odds / (abs_odds + 100.0)


def implied_prob_to_american(prob: float) -> int:
    """Convert implied probability to American odds."""

    if prob <= 0.0 or prob >= 1.0:
        raise ValueError("Probability must be in the open interval (0, 1).")
    if prob >= 0.5:
        return int(round(-100.0 * prob / (1.0 - prob)))
    return int(round(100.0 * (1.0 - prob) / prob))


def vig_remove_proportional(over_price: int, under_price: int) -> tuple[float, float]:
    """Remove vig by proportional normalization."""

    over_raw = american_to_implied_prob(over_price)
    under_raw = american_to_implied_prob(under_price)
    total = over_raw + under_raw
    if total <= 0.0:
        raise ValueError("Invalid implied probabilities.")
    return over_raw / total, under_raw / total


def vig_remove_power(over_price: int, under_price: int) -> tuple[float, float]:
    """Remove vig using a Shin-style power normalization."""

    over_raw = american_to_implied_prob(over_price)
    under_raw = american_to_implied_prob(under_price)
    if over_raw <= 0.0 or under_raw <= 0.0:
        raise ValueError("Invalid implied probabilities.")
    if abs((over_raw + under_raw) - 1.0) < 1e-12:
        return over_raw, under_raw

    low = 1e-6
    high = 10.0
    for _ in range(100):
        mid = (low + high) / 2.0
        value = (over_raw**mid) + (under_raw**mid)
        if value > 1.0:
            low = mid
        else:
            high = mid
    c = (low + high) / 2.0
    over = over_raw**c
    under = under_raw**c
    total = over + under
    return over / total, under / total
