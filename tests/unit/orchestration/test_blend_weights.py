"""Unit tests for orchestration.blend_weights (spec 08)."""

from __future__ import annotations

from pathlib import Path

import pytest

from betting.blend import BlendFit
from orchestration.blend_weights import load_blend_weights, write_blend_weights


def test_committed_config_loads() -> None:
    weights = load_blend_weights(Path("config/blend_weights.yaml"))
    assert 0.0 <= weights["default"] <= 1.0


def test_write_then_load_round_trip(tmp_path: Path) -> None:
    fit = BlendFit(
        weight=0.314, samples=500, log_loss_market=0.69, log_loss_model=0.70, log_loss_blend=0.68
    )
    path = tmp_path / "w.yaml"
    write_blend_weights(path, {"player_points": fit}, default_weight=0.2, source="test")
    assert load_blend_weights(path) == {"default": 0.2, "player_points": 0.31}


def test_load_rejects_missing_default_and_out_of_range(tmp_path: Path) -> None:
    path = tmp_path / "w.yaml"
    path.write_text("weights:\n  player_points: 0.3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_blend_weights(path)
    path.write_text("weights:\n  default: 1.3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_blend_weights(path)
