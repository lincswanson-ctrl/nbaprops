"""Unit tests for scripts.run_pregame."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

import scripts.run_pregame as run_pregame
from orchestration.pregame import StepResult


def test_main_dry_run_skips_step_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run_pregame.sys,
        "argv",
        ["run_pregame.py", "--date", "2026-05-09", "--season", "2025-26", "--dry-run"],
    )

    def _boom(_: date, __: str) -> StepResult:
        raise AssertionError("Step should not execute during dry run")

    monkeypatch.setattr(run_pregame, "_build_steps", lambda **_: [("boom", _boom)])
    run_pregame.main()


def test_main_fail_fast_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run_pregame.sys,
        "argv",
        ["run_pregame.py", "--date", "2026-05-09", "--season", "2025-26", "--fail-fast"],
    )

    def _ok(_: date, __: str) -> StepResult:
        return StepResult(name="ok", details={})

    def _fail(_: date, __: str) -> StepResult:
        raise RuntimeError("expected failure")

    monkeypatch.setattr(run_pregame, "_build_steps", lambda **_: [("ok", _ok), ("fail", _fail)])
    with pytest.raises(SystemExit):
        run_pregame.main()


def test_resolve_date_iso() -> None:
    assert run_pregame._resolve_date("2026-05-09") == date(2026, 5, 9)


def test_dry_run_logs_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run_pregame.sys,
        "argv",
        ["run_pregame.py", "--date", "today", "--season", "2025-26", "--dry-run"],
    )
    captured: list[tuple[str, dict[str, Any]]] = []

    class _Logger:
        def info(self, event: str, **kwargs: Any) -> None:
            captured.append((event, kwargs))

    monkeypatch.setattr(run_pregame, "logger", _Logger())
    monkeypatch.setattr(run_pregame, "_build_steps", lambda **_: [("one", lambda _d, _s: StepResult("one", {}))])
    run_pregame.main()
    assert any(event == "pregame_dry_run" for event, _ in captured)


def test_build_steps_binds_allow_empty_odds(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_odds(
        target_date: date,
        season: str,
        *,
        allow_empty_odds: bool = False,
        books: tuple[str, ...] = ("all",),
    ) -> StepResult:
        captured["target_date"] = target_date
        captured["season"] = season
        captured["allow_empty_odds"] = allow_empty_odds
        captured["books"] = books
        return StepResult(name="2_odds", details={})

    monkeypatch.setattr(run_pregame, "run_odds", _fake_run_odds)
    steps = run_pregame._build_steps(allow_empty_odds=True, books=("draftkings",))
    odds_step = dict(steps)["2_odds"]
    odds_step(date(2026, 5, 10), "2025-26")
    assert captured["allow_empty_odds"] is True
    assert captured["books"] == ("draftkings",)
    assert captured["target_date"] == date(2026, 5, 10)
    assert captured["season"] == "2025-26"


def test_build_steps_binds_books(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_odds(
        target_date: date, season: str, *, allow_empty_odds: bool = False, books: tuple[str, ...] = ("all",)
    ) -> StepResult:
        captured["target_date"] = target_date
        captured["season"] = season
        captured["allow_empty_odds"] = allow_empty_odds
        captured["books"] = books
        return StepResult(name="2_odds", details={})

    monkeypatch.setattr(run_pregame, "run_odds", _fake_run_odds)
    steps = run_pregame._build_steps(allow_empty_odds=False, books=("draftkings", "fanduel"))
    odds_step = dict(steps)["2_odds"]
    odds_step(date(2026, 5, 10), "2025-26")
    assert captured["books"] == ("draftkings", "fanduel")
    assert captured["allow_empty_odds"] is False


def test_parse_books_all() -> None:
    assert run_pregame._parse_books("all") == ("all",)


def test_parse_books_dedupes_and_normalizes() -> None:
    assert run_pregame._parse_books("DraftKings, fanduel, draftkings") == ("draftkings", "fanduel")


def test_log_summary_block_writes_expected_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    class _Logger:
        def info(self, event: str, **kwargs: Any) -> None:
            captured.append((event, kwargs))

    monkeypatch.setattr(run_pregame, "logger", _Logger())
    run_pregame._log_summary_block(
        date(2026, 5, 9),
        {
            "7_summary": {"games": 4, "markets": 12, "edges": 12, "picks": 7},
            "5_edges": {"top_10": [{"edge_pct": 0.05}]},
        },
    )
    assert captured
    assert captured[0][0] == "pregame_summary_block"
    assert "PREGAME SUMMARY - 2026-05-09" in str(captured[0][1]["summary_text"])
