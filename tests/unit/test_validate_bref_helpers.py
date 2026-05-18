"""Unit tests for Basketball Reference validation helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_module():
    path = REPO_ROOT / "scripts" / "validate_nba_vs_bref.py"
    spec = importlib.util.spec_from_file_location("validate_nba_vs_bref_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_normalize_player_id_variants() -> None:
    mod = _load_module()
    n = mod._normalize_player_id
    assert n("203507") == "203507"
    assert n(203507) == "203507"
    assert n(203507.0) == "203507"


def test_stat_within_tolerance_mpg_absolute() -> None:
    mod = _load_module()
    t = mod._stat_within_tolerance
    assert t("mpg", 32.0, 34.0) is True
    assert t("mpg", 28.0, 34.0) is False


def test_stat_within_tolerance_ppg_absolute() -> None:
    mod = _load_module()
    t = mod._stat_within_tolerance
    assert t("ppg", 31.0, 30.4) is True
    assert t("ppg", 25.0, 30.4) is False
