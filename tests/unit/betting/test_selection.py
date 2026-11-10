"""Unit tests for betting.selection (spec 07)."""

from __future__ import annotations

from betting.selection import Candidate, SelectionRules, select_picks

RULES = SelectionRules(
    market_thresholds={"player_points": 0.05},
    default_threshold=0.07,
    min_pick_edge=0.02,
    max_picks_per_player=2,
    max_picks_per_game=3,
)


def _cand(
    edge: float,
    *,
    player: str = "p1",
    game: str = "g1",
    market: str = "player_points",
    price: int | None = -110,
    stale: bool = False,
) -> Candidate:
    return Candidate(
        game_id=game,
        player_id=player,
        market=market,
        side="over",
        edge=edge,
        book="dk",
        price=price,
        stale=stale,
    )


def test_select_picks_keeps_above_threshold_and_sorts_by_edge() -> None:
    out = select_picks([_cand(0.06), _cand(0.10, market="player_rebounds")], RULES)
    assert [d.candidate.edge for d in out] == [0.10, 0.06]
    assert all(d.is_pick for d in out)


def test_select_picks_uses_market_or_default_threshold() -> None:
    out = select_picks([_cand(0.06, market="player_blocks"), _cand(0.04)], RULES)
    assert [d.rejection_reason for d in out] == ["below_threshold", "below_threshold"]


def test_select_picks_rejection_priority() -> None:
    out = select_picks([_cand(0.30, stale=True, price=None), _cand(0.20, price=None)], RULES)
    assert [d.rejection_reason for d in out] == ["stale_odds", "no_under_price"]


def test_select_picks_player_and_game_caps_count_only_picks() -> None:
    cands = [
        _cand(0.20, market="player_points"),
        _cand(0.19, market="player_rebounds"),
        _cand(0.18, market="player_assists"),  # third for p1 -> player_cap
        _cand(0.17, player="p2"),
        _cand(0.16, player="p3"),  # fourth pick in g1 -> game_cap
        _cand(0.15, player="p4", game="g2"),
    ]
    reasons = [d.rejection_reason for d in select_picks(cands, RULES)]
    assert reasons == [None, None, "player_cap", None, "game_cap", None]


def test_select_picks_empty() -> None:
    assert select_picks([], RULES) == []
