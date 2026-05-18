"""Confirm unresolved player aliases by promoting them to confirmed aliases."""

from __future__ import annotations

import argparse

from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger

logger = get_logger(__name__)


def confirm_alias(alias: str, player_id: str) -> None:
    """Mark alias as confirmed for a specific player."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO player_name_aliases (alias, player_id, source, confirmed)
                VALUES (%(alias)s, %(player_id)s, 'manual', TRUE)
                ON CONFLICT (alias) DO UPDATE SET
                    player_id = EXCLUDED.player_id,
                    source = EXCLUDED.source,
                    confirmed = TRUE
                """,
                {"alias": alias, "player_id": player_id},
            )
    logger.info("alias_confirmed", alias=alias, player_id=player_id)


def main() -> int:
    """CLI entrypoint for alias confirmation."""

    parser = argparse.ArgumentParser(description="Confirm injury name alias")
    parser.add_argument("--alias", required=True)
    parser.add_argument("--player-id", required=True)
    args = parser.parse_args()
    confirm_alias(args.alias, args.player_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
