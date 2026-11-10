"""Unit tests for models.walk_forward (spec 07): point-in-time correctness."""

from __future__ import annotations

from datetime import date, timedelta

from models.walk_forward import HistoryIndex, PlayerGame, actual_value, proxy_line

D0 = date(2026, 1, 1)


def _pg(day: int, pts: float, *, played: bool = True, pid: str = "p1") -> PlayerGame:
    return PlayerGame(
        player_id=pid,
        game_id=f"g{day}",
        game_date=D0 + timedelta(days=day),
        played=played,
        stats={"points": pts, "rebounds": 5.0},
    )


def test_prior_played_excludes_target_day_and_later() -> None:
    index = HistoryIndex([_pg(1, 10), _pg(2, 20), _pg(3, 30), _pg(4, 40)])
    prior = index.prior_played("p1", D0 + timedelta(days=3))
    assert [g["points"] for g in prior] == [20, 10]  # newest first, day 3 excluded


def test_prior_played_skips_dnp_and_respects_limits() -> None:
    index = HistoryIndex([_pg(1, 10), _pg(2, 0, played=False), _pg(3, 30)])
    assert [g["points"] for g in index.prior_played("p1", D0 + timedelta(days=9))] == [30, 10]
    assert len(index.prior_played("p1", D0 + timedelta(days=9), max_games=1)) == 1
    assert index.prior_played("p1", D0 + timedelta(days=9), lookback_days=7) == [
        {"points": 30, "rebounds": 5.0}
    ]


def test_prior_played_unknown_player() -> None:
    assert HistoryIndex([]).prior_played("nobody", D0) == []


def test_proxy_line() -> None:
    assert proxy_line([24, 25, 26]) == 25.5
    assert proxy_line([0.4]) == 0.5
    assert proxy_line([]) is None


def test_actual_value_played_dnp_missing() -> None:
    index = HistoryIndex([_pg(1, 22), _pg(2, 0, played=False)])
    assert actual_value("player_points_rebounds", index.game("p1", "g1")) == 27.0
    assert actual_value("player_points", index.game("p1", "g2")) is None
    assert actual_value("player_points", index.game("p1", "missing")) is None
