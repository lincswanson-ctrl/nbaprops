"""Market consensus, vig-free probability and edge side selection (spec 06).

Pure functions only; the pregame orchestrator handles DB I/O.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict

from betting.vig import american_to_implied_prob, implied_prob_to_american, vig_remove_proportional

Side = Literal["over", "under"]


class BookOffer(BaseModel):
    """One book's two-way price on a player prop."""

    model_config = ConfigDict(frozen=True)

    book: str
    line: float
    over_price: int
    under_price: int | None = None


class Consensus(BaseModel):
    """Consensus view of a market at a single line."""

    model_config = ConfigDict(frozen=True)

    line: float
    fair_over: float
    consensus_price: int
    best_over_book: str
    best_over_price: int
    best_under_book: str | None
    best_under_price: int | None
    vig_removed: bool
    books_at_line: int


class SideDecision(BaseModel):
    """Chosen side for a market and the edge magnitude on that side."""

    model_config = ConfigDict(frozen=True)

    side: Side
    edge: float
    book: str | None
    price: int | None


def consensus_line(offers: Sequence[BookOffer]) -> float:
    """Return the line offered by the most books.

    Ties go to the tied line closest to the median of all offered lines (then the lower
    line), so three books at three different lines resolve to the middle one rather than
    systematically to an extreme.

    Raises:
        ValueError: If ``offers`` is empty.
    """
    if not offers:
        raise ValueError("no offers")
    counts = Counter(float(o.line) for o in offers)
    top = max(counts.values())
    tied = [line for line, n in counts.items() if n == top]
    mid = median(float(o.line) for o in offers)
    return min(tied, key=lambda line: (abs(line - mid), line))


def fair_over_prob(offer: BookOffer) -> tuple[float, bool]:
    """Vig-free over probability for one book, and whether vig was actually removed."""
    if offer.under_price is None:
        return american_to_implied_prob(offer.over_price), False
    over, _ = vig_remove_proportional(offer.over_price, offer.under_price)
    return over, True


def consensus_offer(offers: Sequence[BookOffer]) -> Consensus:
    """Build the consensus for one market from all books' offers.

    Only books at the consensus line contribute prices or can be the best book.

    Raises:
        ValueError: If ``offers`` is empty.
    """
    line = consensus_line(offers)
    at_line = sorted((o for o in offers if float(o.line) == line), key=lambda o: o.book)
    fair = [fair_over_prob(o) for o in at_line]
    raw_over = [american_to_implied_prob(o.over_price) for o in at_line]
    best_over = max(at_line, key=lambda o: o.over_price)
    with_under = [o for o in at_line if o.under_price is not None]
    best_under = max(with_under, key=lambda o: int(o.under_price or 0)) if with_under else None
    return Consensus(
        line=line,
        fair_over=sum(p for p, _ in fair) / len(fair),
        consensus_price=implied_prob_to_american(_clamp_open(sum(raw_over) / len(raw_over))),
        best_over_book=best_over.book,
        best_over_price=best_over.over_price,
        best_under_book=best_under.book if best_under else None,
        best_under_price=best_under.under_price if best_under else None,
        vig_removed=all(removed for _, removed in fair),
        books_at_line=len(at_line),
    )


def pick_side(model_prob_over: float, consensus: Consensus, sanity_bound: float) -> SideDecision:
    """Choose over/under by the sign of the edge; return the positive edge magnitude.

    Args:
        model_prob_over: Model probability of the over at ``consensus.line``.
        consensus: Market consensus for the same line.
        sanity_bound: Edge magnitudes are capped at this value.

    Returns:
        The side with positive edge (ties -> over), with that side's best book and price.
        ``book``/``price`` are ``None`` for an under with no under price available.
    """
    edge_over = model_prob_over - consensus.fair_over
    magnitude = min(abs(edge_over), sanity_bound)
    if edge_over >= 0:
        return SideDecision(
            side="over",
            edge=magnitude,
            book=consensus.best_over_book,
            price=consensus.best_over_price,
        )
    return SideDecision(
        side="under",
        edge=magnitude,
        book=consensus.best_under_book,
        price=consensus.best_under_price,
    )


def _clamp_open(prob: float) -> float:
    return min(max(prob, 1e-6), 1.0 - 1e-6)
