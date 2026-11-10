"""Baseline projection model ``baseline_v1`` (spec 05).

Pure functions: recent box-score history -> projected mean/spread -> P(over line).
No I/O; the pregame orchestrator fetches history and writes scores.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

MODEL_VERSION = "baseline_v1"
BASELINE_MAX_GAMES = 20
BASELINE_MIN_GAMES = 5
BASELINE_LOOKBACK_DAYS = 400
BASELINE_HALF_LIFE_GAMES = 8.0
MIN_SIGMA = 0.5
PROB_FLOOR = 0.01
PROB_CEIL = 0.99

MARKET_STATS: dict[str, tuple[str, ...]] = {
    "player_points": ("points",),
    "player_rebounds": ("rebounds",),
    "player_assists": ("assists",),
    "player_threes": ("fg3m",),
    "player_steals": ("steals",),
    "player_blocks": ("blocks",),
    "player_steals_blocks": ("steals", "blocks"),
    "player_points_rebounds_assists": ("points", "rebounds", "assists"),
    "player_points_rebounds": ("points", "rebounds"),
    "player_points_assists": ("points", "assists"),
    "player_rebounds_assists": ("rebounds", "assists"),
}

GameRow = Mapping[str, float | int | None]


class Projection(BaseModel):
    """Projected distribution for one player/market."""

    model_config = ConfigDict(frozen=True)

    mean: float
    sigma: float
    games_used: int


def market_stat_values(market: str, games: Sequence[GameRow]) -> list[float] | None:
    """Return the per-game stat for ``market``, most recent game first.

    Args:
        market: Canonical market name, e.g. ``"player_points"``.
        games: Box-score rows ordered most recent first.

    Returns:
        One value per game, or ``None`` if the market is not supported. Games missing
        any component stat are skipped.
    """
    columns = MARKET_STATS.get(market)
    if columns is None:
        return None
    values: list[float] = []
    for game in games:
        parts = [game.get(col) for col in columns]
        if any(part is None for part in parts):
            continue
        values.append(float(sum(float(p) for p in parts if p is not None)))
    return values


def recency_weights(n: int, half_life: float = BASELINE_HALF_LIFE_GAMES) -> list[float]:
    """Exponential-decay weights, most recent first (weight 1, halving every ``half_life``)."""
    if n <= 0:
        return []
    if half_life <= 0:
        raise ValueError("half_life must be positive")
    decay = 0.5 ** (1.0 / half_life)
    return [decay**i for i in range(n)]


def project(values: Sequence[float], min_games: int = BASELINE_MIN_GAMES) -> Projection | None:
    """Project mean and spread from recent values (most recent first).

    Args:
        values: Per-game stat values, most recent first. Only the first
            ``BASELINE_MAX_GAMES`` are used.
        min_games: Minimum games required to score.

    Returns:
        A ``Projection``, or ``None`` when there are fewer than ``min_games`` values.
    """
    recent = list(values[:BASELINE_MAX_GAMES])
    if len(recent) < min_games:
        return None
    weights = recency_weights(len(recent))
    total = sum(weights)
    mean = sum(w * v for w, v in zip(weights, recent)) / total
    variance = sum(w * (v - mean) ** 2 for w, v in zip(weights, recent)) / total
    # Box-score counts are at least Poisson-dispersed: floor variance at the mean.
    sigma = max(math.sqrt(max(variance, mean)), MIN_SIGMA)
    return Projection(mean=mean, sigma=sigma, games_used=len(recent))


def prob_over(line: float, projection: Projection) -> float:
    """Probability the integer stat finishes over ``line``, excluding pushes.

    Uses a normal approximation with continuity correction. For whole-number lines,
    returns P(over | no push).
    """
    mu, sigma = projection.mean, projection.sigma
    if float(line).is_integer():
        p_over = 1.0 - _norm_cdf((line + 0.5 - mu) / sigma)
        p_under = _norm_cdf((line - 0.5 - mu) / sigma)
        denom = p_over + p_under
        prob = p_over / denom if denom > 0 else 0.5
    else:
        prob = 1.0 - _norm_cdf((math.floor(line) + 0.5 - mu) / sigma)
    return min(max(prob, PROB_FLOOR), PROB_CEIL)


def score_market(market: str, line: float, games: Sequence[GameRow]) -> float | None:
    """End-to-end: history rows -> P(over line) for one market, or ``None`` if unscorable."""
    values = market_stat_values(market, games)
    if values is None:
        return None
    projection = project(values)
    if projection is None:
        return None
    return prob_over(line, projection)


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
