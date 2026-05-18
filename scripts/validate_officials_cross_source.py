"""Validate agreement between prospective and box-score officials."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ValidationSummary:
    """Cross-source validation summary."""

    games: int
    games_with_prospective: int
    games_with_box_score: int
    compared_slots: int
    exact_slots: int
    crew_matches: int


def validate_cross_source(game_date: str) -> ValidationSummary:
    """Compare prospective and retrospective assignments for one date."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    g.game_id,
                    p.crew_chief_id AS p_cc,
                    p.referee_id AS p_ref,
                    p.umpire_id AS p_ump,
                    b.crew_chief_id AS b_cc,
                    b.referee_id AS b_ref,
                    b.umpire_id AS b_ump
                FROM games g
                LEFT JOIN game_officials p
                    ON p.game_id = g.game_id
                   AND p.source = 'nba_official_prospective'
                LEFT JOIN game_officials b
                    ON b.game_id = g.game_id
                   AND b.source = 'nba_stats_box_score'
                WHERE g.date = %(game_date)s
                """,
                {"game_date": game_date},
            )
            rows = list(cur.fetchall())
    games_with_prospective = 0
    games_with_box_score = 0
    compared_slots = 0
    exact_slots = 0
    crew_matches = 0
    for row in rows:
        has_pro = all(row.get(key) is not None for key in ("p_cc", "p_ref", "p_ump"))
        has_box = all(row.get(key) is not None for key in ("b_cc", "b_ref", "b_ump"))
        games_with_prospective += 1 if has_pro else 0
        games_with_box_score += 1 if has_box else 0
        if has_pro and has_box and all(
            str(row[p_key]) == str(row[b_key])
            for p_key, b_key in (("p_cc", "b_cc"), ("p_ref", "b_ref"), ("p_ump", "b_ump"))
        ):
            crew_matches += 1
        for p_key, b_key in (("p_cc", "b_cc"), ("p_ref", "b_ref"), ("p_ump", "b_ump")):
            if row.get(p_key) is None or row.get(b_key) is None:
                continue
            compared_slots += 1
            if str(row[p_key]) == str(row[b_key]):
                exact_slots += 1
    return ValidationSummary(
        games=len(rows),
        games_with_prospective=games_with_prospective,
        games_with_box_score=games_with_box_score,
        compared_slots=compared_slots,
        exact_slots=exact_slots,
        crew_matches=crew_matches,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate officials cross-source agreement")
    parser.add_argument("--date", required=True, help="Game date YYYY-MM-DD")
    return parser


def _run_cli() -> None:
    args = _build_parser().parse_args()
    summary = validate_cross_source(args.date)
    rate = 0.0 if summary.compared_slots == 0 else summary.exact_slots / summary.compared_slots
    logger.info(
        "officials_cross_source_validation",
        game_date=args.date,
        games=summary.games,
        games_with_prospective=summary.games_with_prospective,
        games_with_box_score=summary.games_with_box_score,
        crew_matches=summary.crew_matches,
        compared_slots=summary.compared_slots,
        exact_slots=summary.exact_slots,
        exact_match_rate=rate,
    )


if __name__ == "__main__":
    _run_cli()
