"""Unit tests for readiness check helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.readiness_checks import _latest_file, check_injury_disagreement


def test_latest_file_returns_none_for_missing_dir(tmp_path: Path) -> None:
    assert _latest_file(tmp_path / "missing") is None


def test_latest_file_returns_newest_file(tmp_path: Path) -> None:
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    one.write_text("a", encoding="utf-8")
    two.write_text("b", encoding="utf-8")
    assert _latest_file(tmp_path) in {one, two}


def test_check_injury_disagreement_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.readiness_checks as readiness

    monkeypatch.setattr(readiness, "validate_sources", lambda _: (100, 3))
    result = check_injury_disagreement(5.0)
    assert result.passed is True


def test_check_injury_disagreement_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.readiness_checks as readiness

    monkeypatch.setattr(readiness, "validate_sources", lambda _: (100, 8))
    result = check_injury_disagreement(5.0)
    assert result.passed is False
