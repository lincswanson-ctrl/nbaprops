"""Pick selection rules shared by the live pregame run and the backtest (spec 07).

Pure: candidates in, decisions out. Keeping one implementation guarantees the backtest
replays exactly the thresholds and exposure caps used live.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from betting.market import Side


class SelectionRules(BaseModel):
    """Thresholds and exposure caps applied when turning edges into picks."""

    model_config = ConfigDict(frozen=True)

    market_thresholds: Mapping[str, float]
    default_threshold: float
    min_pick_edge: float
    max_picks_per_player: int
    max_picks_per_game: int


class Candidate(BaseModel):
    """One scored market on its chosen side."""

    model_config = ConfigDict(frozen=True)

    game_id: str
    player_id: str
    market: str
    side: Side
    edge: float
    book: str | None
    price: int | None
    stale: bool = False


class Decision(BaseModel):
    """Selection outcome for one candidate."""

    model_config = ConfigDict(frozen=True)

    candidate: Candidate
    decision: str  # "keep" | "reject"
    rejection_reason: str | None
    is_pick: bool


def select_picks(candidates: Sequence[Candidate], rules: SelectionRules) -> list[Decision]:
    """Apply rejection rules in priority order, highest edge first.

    Order: stale odds, missing price (under with no under price), market threshold,
    per-player cap, per-game cap. A kept candidate becomes a pick when its edge is at
    least ``min_pick_edge``; only picks count toward the caps.

    Returns:
        One decision per candidate, sorted by edge descending (stable for ties).
    """
    per_player: dict[str, int] = {}
    per_game: dict[str, int] = {}
    decisions: list[Decision] = []
    for cand in sorted(candidates, key=lambda c: c.edge, reverse=True):
        reason = _rejection_reason(cand, rules, per_player, per_game)
        is_pick = reason is None and cand.edge >= rules.min_pick_edge
        if is_pick:
            per_player[cand.player_id] = per_player.get(cand.player_id, 0) + 1
            per_game[cand.game_id] = per_game.get(cand.game_id, 0) + 1
        decisions.append(
            Decision(
                candidate=cand,
                decision="keep" if reason is None else "reject",
                rejection_reason=reason,
                is_pick=is_pick,
            )
        )
    return decisions


def _rejection_reason(
    cand: Candidate,
    rules: SelectionRules,
    per_player: Mapping[str, int],
    per_game: Mapping[str, int],
) -> str | None:
    if cand.stale:
        return "stale_odds"
    if cand.price is None:
        return "no_under_price"
    if cand.edge < rules.market_thresholds.get(cand.market, rules.default_threshold):
        return "below_threshold"
    if per_player.get(cand.player_id, 0) >= rules.max_picks_per_player:
        return "player_cap"
    if per_game.get(cand.game_id, 0) >= rules.max_picks_per_game:
        return "game_cap"
    return None
