"""Validate loaded games by comparing summed player points to game totals."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger
from data_pipeline.team_ids import normalize_team_id

logger = get_logger(__name__)


@dataclass(frozen=True)
class ValidationMismatch:
    """Represents one mismatch between player sums and game score."""

    game_id: str
    expected_home: int | None
    expected_away: int | None
    actual_home: int
    actual_away: int


def validate_random_games(limit: int = 20) -> list[ValidationMismatch]:
    """Sample loaded games and verify player stat sums match game-level scores."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT g.game_id, g.home_team_id, g.away_team_id, g.home_score, g.away_score
                FROM games g
                WHERE g.home_score IS NOT NULL
                  AND g.away_score IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM player_game_stats pgs
                      WHERE pgs.game_id = g.game_id
                        AND pgs.team_id IS NOT NULL
                  )
                ORDER BY RANDOM()
                LIMIT %(limit)s
                """,
                {"limit": limit},
            )
            games = cur.fetchall()
            mismatches: list[ValidationMismatch] = []
            for game in games:
                cur.execute(
                    """
                    SELECT team_id, COALESCE(SUM(points), 0) AS pts
                    FROM player_game_stats
                    WHERE game_id = %(game_id)s
                    GROUP BY team_id
                    """,
                    {"game_id": game["game_id"]},
                )
                rows = cur.fetchall()
                by_team = {normalize_team_id(row["team_id"]): int(row["pts"]) for row in rows}
                actual_home = by_team.get(normalize_team_id(game["home_team_id"]), 0)
                actual_away = by_team.get(normalize_team_id(game["away_team_id"]), 0)
                if actual_home != (game["home_score"] or 0) or actual_away != (game["away_score"] or 0):
                    mismatches.append(
                        ValidationMismatch(
                            game_id=str(game["game_id"]),
                            expected_home=game["home_score"],
                            expected_away=game["away_score"],
                            actual_home=actual_home,
                            actual_away=actual_away,
                        )
                    )
    return mismatches


def main() -> int:
    """Run validation and print a human-readable report."""

    mismatches = validate_random_games()
    if not mismatches:
        logger.info("nba_load_validation_passed", mismatches=0)
        return 0
    logger.warning("nba_load_validation_failed", mismatch_count=len(mismatches))
    for mismatch in mismatches:
        logger.warning(
            "nba_load_validation_mismatch",
            game_id=mismatch.game_id,
            expected_home=mismatch.expected_home,
            expected_away=mismatch.expected_away,
            actual_home=mismatch.actual_home,
            actual_away=mismatch.actual_away,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
