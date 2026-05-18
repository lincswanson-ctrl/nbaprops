"""Cross-source injury status agreement validator."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger

logger = get_logger(__name__)


def validate_sources(at_timestamp: datetime) -> tuple[int, int]:
    """Return total players and disagreement count for latest statuses per source."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (player_id, source)
                        player_id, source, status, status_date
                    FROM injury_status
                    WHERE status_date <= %(at_timestamp)s
                    ORDER BY player_id, source, status_date DESC
                )
                SELECT l1.player_id, l1.status AS rotowire_status, l2.status AS espn_status
                FROM latest l1
                JOIN latest l2 ON l1.player_id = l2.player_id
                WHERE l1.source = 'rotowire' AND l2.source = 'espn'
                """,
                {"at_timestamp": at_timestamp},
            )
            rows = cur.fetchall()
    total = len(rows)
    disagreements = sum(1 for row in rows if row["rotowire_status"] != row["espn_status"])
    return total, disagreements


def build_validation_snapshot(at_timestamp: datetime) -> dict[str, object]:
    """Build a detailed snapshot of source agreement/disagreement."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (player_id, source)
                        player_id, source, status, status_date
                    FROM injury_status
                    WHERE status_date <= %(at_timestamp)s
                    ORDER BY player_id, source, status_date DESC
                )
                SELECT
                    p.name AS player_name,
                    rw.status AS rotowire_status,
                    rw.status_date AS rotowire_ts,
                    es.status AS espn_status,
                    es.status_date AS espn_ts
                FROM latest rw
                JOIN latest es ON rw.player_id = es.player_id
                JOIN players p ON p.player_id = rw.player_id
                WHERE rw.source = 'rotowire' AND es.source = 'espn'
                ORDER BY p.name
                """,
                {"at_timestamp": at_timestamp},
            )
            both_rows = cur.fetchall()
            cur.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (player_id, source)
                        player_id, source, status, status_date
                    FROM injury_status
                    WHERE status_date <= %(at_timestamp)s
                    ORDER BY player_id, source, status_date DESC
                )
                SELECT p.name AS player_name, source
                FROM latest
                JOIN players p USING (player_id)
                WHERE player_id NOT IN (
                    SELECT player_id
                    FROM latest
                    GROUP BY player_id
                    HAVING COUNT(*) >= 2
                )
                ORDER BY source, p.name
                """,
                {"at_timestamp": at_timestamp},
            )
            one_source_rows = cur.fetchall()

    disagreements: list[dict[str, object]] = []
    for row in both_rows:
        if row["rotowire_status"] == row["espn_status"]:
            continue
        disagreements.append(
            {
                "player_name": row["player_name"],
                "rotowire_status": row["rotowire_status"],
                "espn_status": row["espn_status"],
                "rotowire_ts": row["rotowire_ts"],
                "espn_ts": row["espn_ts"],
            }
        )
    rotowire_only = [row["player_name"] for row in one_source_rows if row["source"] == "rotowire"]
    espn_only = [row["player_name"] for row in one_source_rows if row["source"] == "espn"]
    return {
        "both_rows": both_rows,
        "disagreements": disagreements,
        "rotowire_only": rotowire_only,
        "espn_only": espn_only,
    }


def main() -> int:
    """CLI entrypoint for source-disagreement validation."""

    parser = argparse.ArgumentParser(description="Validate injury-source agreement")
    parser.add_argument("--at", help="UTC timestamp ISO-8601; defaults to now")
    parser.add_argument(
        "--min-shared-players",
        type=int,
        default=20,
        help="Skip failure if fewer than this many players exist in both sources.",
    )
    args = parser.parse_args()
    timestamp = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
    total, disagreements = validate_sources(timestamp)
    snapshot = build_validation_snapshot(timestamp)
    rate = (disagreements / total * 100.0) if total else 0.0
    logger.info(
        "injury_source_validation",
        timestamp=timestamp.isoformat(),
        total_players=total,
        disagreements=disagreements,
        disagreement_rate=round(rate, 2),
    )
    for row in snapshot["disagreements"]:
        logger.warning(
            "injury_source_disagreement_player",
            player_name=row["player_name"],
            rotowire_status=row["rotowire_status"],
            espn_status=row["espn_status"],
            rotowire_ts=str(row["rotowire_ts"]),
            espn_ts=str(row["espn_ts"]),
        )
    logger.info(
        "injury_source_one_sided",
        rotowire_only_count=len(snapshot["rotowire_only"]),
        espn_only_count=len(snapshot["espn_only"]),
    )
    if total < args.min_shared_players:
        logger.warning(
            "injury_source_validation_low_sample",
            total_players=total,
            min_shared_players=args.min_shared_players,
        )
        return 0
    return 0 if rate < 5.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
