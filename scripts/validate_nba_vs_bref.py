"""Validate loaded season totals against Basketball Reference fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger

logger = get_logger(__name__)


def _normalize_player_id(value: object) -> str:
    """Stable dict key for NBA player_id across int/str/float from Postgres."""

    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value))
    s = str(value).strip()
    try:
        if "." in s:
            return str(int(float(s)))
        return str(int(s))
    except ValueError:
        return s


def _gp_within_tolerance(db_gp: float, expected_gp: float) -> bool:
    """Allow small drift between fixture reference GP and DB aggregates."""

    if expected_gp <= 0:
        return True
    diff = abs(db_gp - expected_gp)
    if expected_gp < 10:
        return diff <= 2.0
    return diff / expected_gp <= 0.10


def _stat_within_tolerance(field: str, observed: float, expected: float) -> bool:
    """Relative tolerance plus absolute floor (minute parsing / rounding differs by source)."""

    if expected == 0:
        return True
    rel = abs(observed - expected) / expected
    if field == "mpg":
        return rel <= 0.10 or abs(observed - expected) <= 2.5
    if field == "ppg":
        return rel <= 0.10 or abs(observed - expected) <= 1.5
    return rel <= 0.10


def _load_fixture(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _season_totals_from_db(season: str) -> dict[str, dict[str, float]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.player_id,
                    p.name,
                    COUNT(*)::float AS gp,
                    AVG(COALESCE(pgs.minutes, 0))::float AS mpg,
                    AVG(COALESCE(pgs.points, 0))::float AS ppg
                FROM player_game_stats pgs
                JOIN games g ON g.game_id = pgs.game_id
                JOIN players p ON p.player_id = pgs.player_id
                WHERE g.season = %(season)s
                GROUP BY p.player_id, p.name
                """,
                {"season": season},
            )
            rows = cur.fetchall()
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        key = _normalize_player_id(row["player_id"])
        out[key] = {"gp": float(row["gp"]), "mpg": float(row["mpg"]), "ppg": float(row["ppg"])}
    return out


def validate_against_fixture(season: str, fixture_path: Path) -> tuple[int, int]:
    """Return (checked_players, mismatches) against BRef fixture."""

    fixture_rows = _load_fixture(fixture_path)
    db_totals = _season_totals_from_db(season)
    checked = 0
    mismatches = 0
    for row in fixture_rows:
        player_id = _normalize_player_id(row["player_id"])
        db_row = db_totals.get(player_id)
        if db_row is None:
            mismatches += 1
            continue
        checked += 1
        gp_expected = float(row["gp"])
        if not _gp_within_tolerance(db_row["gp"], gp_expected):
            logger.warning(
                "bref_validation_mismatch",
                player_id=player_id,
                field="gp",
                expected=gp_expected,
                observed=db_row["gp"],
            )
            mismatches += 1
            continue
        for field in ("mpg", "ppg"):
            expected = float(row[field])
            observed = float(db_row[field])
            if not _stat_within_tolerance(field, observed, expected):
                logger.warning(
                    "bref_validation_mismatch",
                    player_id=player_id,
                    field=field,
                    expected=expected,
                    observed=observed,
                )
                mismatches += 1
                break
    return checked, mismatches


def main() -> int:
    """CLI entrypoint for BRef validation."""

    parser = argparse.ArgumentParser(description="Validate season totals vs BRef fixture")
    parser.add_argument("--season", required=True)
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/nba_stats/bref_2024_25_totals.json",
        help="Path to known-good Basketball Reference totals fixture",
    )
    args = parser.parse_args()
    checked, mismatches = validate_against_fixture(args.season, Path(args.fixture))
    logger.info("bref_validation_complete", season=args.season, checked=checked, mismatches=mismatches)
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
