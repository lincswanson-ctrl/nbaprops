"""Unit tests for orchestration.backtest (spec 07), using in-memory data only."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from betting.selection import SelectionRules
from models.walk_forward import HistoryIndex, PlayerGame
from orchestration.backtest import (
    BetRecord,
    bankroll_curve,
    blend_samples,
    fit_blend_weights,
    build_candidates,
    calibration_samples,
    run_betting,
    summarize_bets,
)

D0 = date(2026, 1, 1)
TARGET = D0 + timedelta(days=30)
RULES = SelectionRules(
    market_thresholds={},
    default_threshold=0.05,
    min_pick_edge=0.02,
    max_picks_per_player=2,
    max_picks_per_game=6,
)


def _history(points: list[float], target_points: float | None) -> HistoryIndex:
    games = [
        PlayerGame(
            player_id="p1",
            game_id=f"h{i}",
            game_date=D0 + timedelta(days=i),
            played=True,
            stats={"points": p},
        )
        for i, p in enumerate(points)
    ]
    games.append(
        PlayerGame(
            player_id="p1",
            game_id="target",
            game_date=TARGET,
            played=target_points is not None,
            stats={"points": target_points},
        )
    )
    return HistoryIndex(games)


def _line(line: float, over: int = -110, under: int = -110, book: str = "dk") -> dict[str, Any]:
    return {
        "game_id": "target",
        "player_id": "p1",
        "market": "player_points",
        "book": book,
        "line": line,
        "over_price": over,
        "under_price": under,
        "game_date": TARGET,
    }


def test_run_betting_picks_under_on_inflated_line_and_grades_it() -> None:
    index = _history([20, 21, 19, 20, 22, 18, 20, 21], target_points=17)
    bets = run_betting(index, [_line(26.5)], RULES)
    assert len(bets) == 1
    bet = bets[0]
    assert (bet.side, bet.line, bet.outcome) == ("under", 26.5, "win")
    assert bet.profit == pytest.approx(100 / 110)
    assert bet.model_prob > 0.5


def test_run_betting_dnp_is_void() -> None:
    index = _history([20, 21, 19, 20, 22, 18, 20, 21], target_points=None)
    bets = run_betting(index, [_line(26.5)], RULES)
    assert [b.outcome for b in bets] == ["void"]


def test_build_candidates_never_sees_target_game() -> None:
    # A 60-point target game must not leak into its own projection.
    index = _history([20] * 8, target_points=60)
    scored = build_candidates(index, [_line(20.5)])
    ((cand, ctx),) = scored[TARGET]
    assert ctx["p"] < 0.5


def test_build_candidates_skips_players_without_history() -> None:
    assert build_candidates(_history([], target_points=20), [_line(20.5)]) == {}


def test_calibration_samples_three_lines_per_player_game() -> None:
    index = _history([20, 21, 19, 20, 22, 18, 20, 21], target_points=25)
    target = index.game("p1", "target")
    assert target is not None
    probs, outcomes, errors = calibration_samples(index, [target], "player_points")
    assert len(probs) == 3 and len(errors) == 1
    assert probs[0] > probs[1] > probs[2]  # higher line -> lower P(over)
    assert outcomes == [1, 1, 1]


def _bet(day: int, outcome: str, profit: float, side: str = "over", edge: float = 0.06) -> BetRecord:
    return BetRecord(
        game_date=D0 + timedelta(days=day),
        game_id="g",
        player_id="p",
        market="player_points",
        side=side,
        line=20.5,
        book="dk",
        price=-110,
        model_prob=0.6,
        fair_prob=0.5,
        edge=edge,
        actual=None,
        outcome=outcome,
        profit=profit,
    )


def test_summarize_bets_roi_excludes_voids() -> None:
    bets = [_bet(1, "win", 1.0), _bet(1, "loss", -1.0, side="under"), _bet(2, "void", 0.0)]
    summary = summarize_bets(bets)
    assert (summary["bets"], summary["wins"], summary["voids"]) == (3, 1, 1)
    assert summary["roi"] == 0.0
    assert summary["by_side"]["under"]["losses"] == 1
    assert summary["by_edge_bucket"]["0.05-0.08"]["bets"] == 3


def test_summarize_bets_empty() -> None:
    summary = summarize_bets([])
    assert summary["bets"] == 0 and summary["roi"] is None


def test_bankroll_curve_daily() -> None:
    curve = bankroll_curve([_bet(2, "loss", -1.0), _bet(1, "win", 0.5), _bet(1, "win", 0.5)])
    assert curve == [
        {"date": "2026-01-02", "bankroll": 101.0},
        {"date": "2026-01-03", "bankroll": 100.0},
    ]


def test_run_betting_market_only_weight_makes_no_picks() -> None:
    index = _history([20, 21, 19, 20, 22, 18, 20, 21], target_points=17)
    assert run_betting(index, [_line(26.5)], RULES, weights={"default": 0.0}) == []


def test_run_betting_blend_shrinks_edge() -> None:
    index = _history([20, 21, 19, 20, 22, 18, 20, 21], target_points=17)
    raw = run_betting(index, [_line(26.5)], RULES)[0]
    blended = run_betting(index, [_line(26.5)], RULES, weights={"default": 0.5})[0]
    assert 0 < blended.edge < raw.edge


def test_blend_samples_skip_dnp_and_push() -> None:
    played = _history([20, 21, 19, 20, 22, 18, 20, 21], target_points=17)
    assert len(blend_samples(played, [_line(26.5)])["player_points"]) == 1
    assert blend_samples(played, [_line(17.0)]) == {}  # push
    dnp = _history([20, 21, 19, 20, 22, 18, 20, 21], target_points=None)
    assert blend_samples(dnp, [_line(26.5)]) == {}


def test_fit_blend_weights_requires_min_samples() -> None:
    index = _history([20, 21, 19, 20, 22, 18, 20, 21], target_points=17)
    samples = blend_samples(index, [_line(26.5)])
    assert fit_blend_weights(samples, min_samples=2) == ({}, None)
    fits, pooled = fit_blend_weights(samples, min_samples=1)
    assert set(fits) == {"player_points"} and pooled is not None
