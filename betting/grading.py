"""Grade settled prop bets (spec 07). Pure functions."""

from __future__ import annotations

from typing import Literal

from betting.market import Side

Outcome = Literal["win", "loss", "push", "void"]


def grade_bet(side: Side, line: float, actual: float | None) -> Outcome:
    """Settle one over/under bet.

    Args:
        side: ``"over"`` or ``"under"``.
        line: The line bet.
        actual: Final stat value, or ``None`` if the player did not play (bet voided).

    Returns:
        ``"win"``, ``"loss"``, ``"push"`` (actual equals a whole-number line) or ``"void"``.
    """
    if actual is None:
        return "void"
    if actual == line:
        return "push"
    went_over = actual > line
    return "win" if went_over == (side == "over") else "loss"


def profit_units(outcome: Outcome, american_price: int, stake: float = 1.0) -> float:
    """Profit in units for a settled bet at ``american_price``; pushes and voids return 0.

    Raises:
        ValueError: If ``american_price`` is between -100 and +100 exclusive or zero.
    """
    if -100 < american_price < 100:
        raise ValueError(f"invalid American price: {american_price}")
    if outcome in ("push", "void"):
        return 0.0
    if outcome == "loss":
        return -stake
    payout = american_price / 100.0 if american_price > 0 else 100.0 / abs(american_price)
    return stake * payout
