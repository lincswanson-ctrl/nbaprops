"""Load and write model/market blend weights (spec 08)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from betting.blend import DEFAULT_KEY, BlendFit


def load_blend_weights(path: Path) -> dict[str, float]:
    """Read ``weights`` from a blend-weights YAML file.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If there is no ``default`` weight or a weight is outside [0, 1].
    """
    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = payload.get("weights") or {}
    weights = {str(k): float(v) for k, v in raw.items()}
    if DEFAULT_KEY not in weights:
        raise ValueError(f"{path}: blend weights need a '{DEFAULT_KEY}' entry")
    bad = {k: v for k, v in weights.items() if not 0.0 <= v <= 1.0}
    if bad:
        raise ValueError(f"{path}: blend weights must be in [0, 1]: {bad}")
    return weights


def write_blend_weights(
    path: Path, fits: Mapping[str, BlendFit], *, default_weight: float, source: str
) -> None:
    """Write fitted weights (plus fit diagnostics) in the format ``load_blend_weights`` reads."""
    payload = {
        "fitted": True,
        "source": source,
        "weights": {DEFAULT_KEY: round(default_weight, 2)}
        | {market: round(fit.weight, 2) for market, fit in sorted(fits.items())},
        "diagnostics": {market: fit.model_dump() for market, fit in sorted(fits.items())},
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
