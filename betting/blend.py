"""Blend model and market probabilities (spec 08). Pure functions.

``logit(p) = w * logit(p_model) + (1 - w) * logit(p_market)``: w=0 trusts the de-vigged
market entirely (no edge is ever found), w=1 is the raw model.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from models.evaluation import log_loss

PROB_EPS = 1e-6
WEIGHT_GRID_STEPS = 100  # w in {0.00, 0.01, ..., 1.00}
DEFAULT_KEY = "default"


class BlendSample(BaseModel):
    """One market observation for fitting the blend weight."""

    model_config = ConfigDict(frozen=True)

    p_model: float
    p_market: float
    outcome: int  # 1 = over hit, 0 = under hit (pushes/voids excluded)


class BlendFit(BaseModel):
    """Result of fitting the blend weight for one market."""

    model_config = ConfigDict(frozen=True)

    weight: float
    samples: int
    log_loss_market: float
    log_loss_model: float
    log_loss_blend: float


def blend_prob(p_model: float, p_market: float, weight: float) -> float:
    """Blend two over-probabilities in log-odds space.

    Raises:
        ValueError: If ``weight`` is outside [0, 1].
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"blend weight must be in [0, 1], got {weight}")
    z = weight * _logit(p_model) + (1.0 - weight) * _logit(p_market)
    return 1.0 / (1.0 + math.exp(-z))


def fit_weight(samples: Sequence[BlendSample]) -> BlendFit:
    """Grid-search the weight minimizing log loss (ties -> smaller weight, i.e. trust market).

    Raises:
        ValueError: If ``samples`` is empty.
    """
    if not samples:
        raise ValueError("no samples to fit")
    outcomes = [s.outcome for s in samples]
    best_w, best_loss = 0.0, math.inf
    for step in range(WEIGHT_GRID_STEPS + 1):
        w = step / WEIGHT_GRID_STEPS
        loss = log_loss([blend_prob(s.p_model, s.p_market, w) for s in samples], outcomes)
        if loss < best_loss - 1e-12:
            best_w, best_loss = w, loss
    return BlendFit(
        weight=best_w,
        samples=len(samples),
        log_loss_market=log_loss([s.p_market for s in samples], outcomes),
        log_loss_model=log_loss([s.p_model for s in samples], outcomes),
        log_loss_blend=best_loss,
    )


def weight_for_market(weights: Mapping[str, float], market: str) -> float:
    """Market-specific weight, else the ``default`` entry.

    Raises:
        KeyError: If neither the market nor ``default`` is present.
    """
    if market in weights:
        return weights[market]
    return weights[DEFAULT_KEY]


def _logit(p: float) -> float:
    q = min(max(p, PROB_EPS), 1.0 - PROB_EPS)
    return math.log(q / (1.0 - q))
