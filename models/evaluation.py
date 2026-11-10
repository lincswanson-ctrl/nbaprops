"""Probability-forecast metrics for backtests (spec 07). Pure functions."""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

LOG_LOSS_EPS = 1e-6


class ReliabilityBin(BaseModel):
    """One calibration bucket: predicted vs observed frequency."""

    model_config = ConfigDict(frozen=True)

    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Mean squared error between probabilities and 0/1 outcomes.

    Raises:
        ValueError: On empty or mismatched inputs.
    """
    _check(probs, outcomes)
    return sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / len(probs)


def log_loss(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Mean negative log-likelihood, with probabilities clipped to avoid infinities."""
    _check(probs, outcomes)
    total = 0.0
    for p, y in zip(probs, outcomes):
        q = min(max(p, LOG_LOSS_EPS), 1.0 - LOG_LOSS_EPS)
        total -= math.log(q) if y else math.log(1.0 - q)
    return total / len(probs)


def brier_skill_vs_base_rate(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Brier skill score against always predicting the observed base rate.

    Positive = better than the base rate; 0 = no skill; negative = worse. Returns 0.0
    when every outcome is identical (reference Brier is zero).
    """
    _check(probs, outcomes)
    rate = sum(outcomes) / len(outcomes)
    reference = rate * (1.0 - rate)
    if reference == 0:
        return 0.0
    return 1.0 - brier_score(probs, outcomes) / reference


def reliability_bins(
    probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10
) -> list[ReliabilityBin]:
    """Equal-width calibration buckets over [0, 1]; empty buckets are omitted."""
    _check(probs, outcomes)
    if n_bins <= 0:
        raise ValueError("n_bins must be positive")
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, outcomes):
        buckets[min(int(p * n_bins), n_bins - 1)].append((p, y))
    result: list[ReliabilityBin] = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue
        result.append(
            ReliabilityBin(
                lower=i / n_bins,
                upper=(i + 1) / n_bins,
                count=len(bucket),
                mean_predicted=sum(p for p, _ in bucket) / len(bucket),
                observed_rate=sum(y for _, y in bucket) / len(bucket),
            )
        )
    return result


def _check(probs: Sequence[float], outcomes: Sequence[int]) -> None:
    if not probs or len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must be non-empty and the same length")
