"""Unit tests for betting.market (spec 06)."""

from __future__ import annotations

import pytest

from betting.market import BookOffer, consensus_line, consensus_offer, fair_over_prob, pick_side

BOUND = 0.5


def _offer(book: str, line: float, over: int, under: int | None = -110) -> BookOffer:
    return BookOffer(book=book, line=line, over_price=over, under_price=under)


# --- consensus_line -----------------------------------------------------------------------


def test_consensus_line_most_common() -> None:
    offers = [_offer("a", 24.5, -110), _offer("b", 24.5, -115), _offer("c", 25.5, -110)]
    assert consensus_line(offers) == 24.5


def test_consensus_line_two_way_tie_takes_lower_line() -> None:
    assert consensus_line([_offer("a", 25.5, -110), _offer("b", 24.5, -110)]) == 24.5


def test_consensus_line_three_different_lines_takes_middle() -> None:
    offers = [_offer("a", 23.5, -110), _offer("b", 24.5, -110), _offer("c", 25.5, -110)]
    assert consensus_line(offers) == 24.5


def test_consensus_line_empty_raises() -> None:
    with pytest.raises(ValueError):
        consensus_line([])


# --- fair_over_prob -----------------------------------------------------------------------


def test_fair_over_prob_symmetric_prices_is_half() -> None:
    prob, removed = fair_over_prob(_offer("a", 20.5, -110, -110))
    assert prob == pytest.approx(0.5)
    assert removed


def test_fair_over_prob_favorite() -> None:
    prob, removed = fair_over_prob(_offer("a", 20.5, -150, 120))
    assert 0.5 < prob < 0.6  # raw 0.600 minus vig share
    assert removed


def test_fair_over_prob_missing_under_keeps_vig() -> None:
    prob, removed = fair_over_prob(_offer("a", 20.5, -110, None))
    assert prob == pytest.approx(110 / 210)
    assert not removed


# --- consensus_offer ----------------------------------------------------------------------


def test_consensus_offer_ignores_books_at_other_lines() -> None:
    offers = [
        _offer("draftkings", 24.5, -110, -110),
        _offer("fanduel", 24.5, -105, -115),
        _offer("betmgm", 26.5, 200, -250),  # different line: must not be "best over"
    ]
    c = consensus_offer(offers)
    assert c.line == 24.5
    assert c.books_at_line == 2
    assert (c.best_over_book, c.best_over_price) == ("fanduel", -105)
    assert (c.best_under_book, c.best_under_price) == ("draftkings", -110)
    assert c.vig_removed
    # fanduel prices the over cheaper (-105/-115), pulling fair over just below 0.5.
    assert 0.48 < c.fair_over < 0.5


def test_consensus_offer_price_from_probabilities_not_american_average() -> None:
    # Averaging American odds (+150, -110) gives a meaningless +20. Averaging implied
    # probabilities (0.400, 0.524 -> 0.462) gives about +116.
    c = consensus_offer([_offer("a", 10.5, 150, -180), _offer("b", 10.5, -110, -110)])
    assert 110 < c.consensus_price < 125


def test_consensus_offer_best_price_tie_breaks_by_book_name() -> None:
    c = consensus_offer([_offer("zbook", 5.5, -110), _offer("abook", 5.5, -110)])
    assert c.best_over_book == "abook"


def test_consensus_offer_no_under_prices() -> None:
    c = consensus_offer([_offer("a", 5.5, -110, None), _offer("b", 5.5, -120, None)])
    assert c.best_under_book is None and c.best_under_price is None
    assert not c.vig_removed


def test_consensus_offer_partial_under_prices_not_fully_devigged() -> None:
    c = consensus_offer([_offer("a", 5.5, -110, -110), _offer("b", 5.5, -110, None)])
    assert c.best_under_book == "a"
    assert not c.vig_removed


# --- pick_side ----------------------------------------------------------------------------


def test_pick_side_over() -> None:
    d = pick_side(0.60, consensus_offer([_offer("dk", 20.5, -110)]), BOUND)
    assert d.side == "over"
    assert d.edge == pytest.approx(0.10)
    assert (d.book, d.price) == ("dk", -110)


def test_pick_side_under_has_positive_edge_and_under_price() -> None:
    d = pick_side(0.35, consensus_offer([_offer("dk", 20.5, -110, -105)]), BOUND)
    assert d.side == "under"
    assert d.edge > 0
    assert (d.book, d.price) == ("dk", -105)


def test_pick_side_under_without_price_has_no_book() -> None:
    d = pick_side(0.30, consensus_offer([_offer("dk", 20.5, -110, None)]), BOUND)
    assert d.side == "under"
    assert d.book is None and d.price is None


def test_pick_side_edge_capped_by_sanity_bound() -> None:
    d = pick_side(0.99, consensus_offer([_offer("dk", 20.5, -110)]), 0.2)
    assert d.edge == pytest.approx(0.2)
