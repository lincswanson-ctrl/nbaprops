"""Load injury snapshot records into injury_status timeline table."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from data_pipeline.config import get_settings
from data_pipeline.db import get_connection
from data_pipeline.loaders.injury_status_mapping import normalize_status
from data_pipeline.loaders.name_resolution import resolve_player_name
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.injuries import InjuryRecord

logger = get_logger(__name__)


@dataclass(frozen=True)
class LoadSummary:
    """Summary counters for one injury snapshot load."""

    total_records: int
    inserted: int
    deduped: int
    unresolved_names: int
    unmapped_statuses: int


class InjuryLoader:
    """Loader for Rotowire/ESPN injury snapshots."""

    def load_snapshot(self, records: list[InjuryRecord]) -> LoadSummary:
        inserted = 0
        deduped = 0
        unresolved = 0
        unmapped = 0
        with get_connection() as conn:
            with conn.cursor() as cur:
                for record in records:
                    player_id = resolve_player_name(record.raw_player_name, record.team_abbr)
                    if player_id is None:
                        unresolved += 1
                        continue
                    normalized = normalize_status(record.raw_status)
                    if normalized == "unknown":
                        unmapped += 1
                    if _is_duplicate(cur, player_id, record.source, normalized, record.scraped_at):
                        deduped += 1
                        continue
                    cur.execute(
                        """
                        INSERT INTO injury_status (
                            player_id, status_date, status, body_part, source, raw_status
                        ) VALUES (
                            %(player_id)s, %(status_date)s, %(status)s, %(body_part)s, %(source)s, %(raw_status)s
                        )
                        ON CONFLICT (player_id, status_date, source) DO NOTHING
                        """,
                        {
                            "player_id": player_id,
                            "status_date": record.scraped_at,
                            "status": normalized,
                            "body_part": record.body_part,
                            "source": record.source,
                            "raw_status": record.raw_status,
                        },
                    )
                    inserted += 1
        return LoadSummary(
            total_records=len(records),
            inserted=inserted,
            deduped=deduped,
            unresolved_names=unresolved,
            unmapped_statuses=unmapped,
        )


def _is_duplicate(
    cur: object,
    player_id: str,
    source: str,
    status: str,
    scraped_at: datetime,
) -> bool:
    today = scraped_at.astimezone(timezone.utc).date()
    cur.execute(
        """
        SELECT status
        FROM injury_status
        WHERE player_id = %(player_id)s
          AND source = %(source)s
          AND status_date::date = %(status_date)s
        ORDER BY status_date DESC
        LIMIT 1
        """,
        {"player_id": player_id, "source": source, "status_date": today},
    )
    row = cur.fetchone()
    return row is not None and row["status"] == status


def _load_records_from_raw(source: str) -> list[InjuryRecord]:
    root = get_settings().raw_storage_path / "injuries" / source
    if not root.exists():
        return []
    latest = sorted(root.glob("*"))[-1]
    from data_pipeline.scrapers.injuries import ESPNScraper, RotowireScraper

    parser = RotowireScraper() if source == "rotowire" else ESPNScraper()
    if latest.suffix == ".json":
        payload = json.loads(latest.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [InjuryRecord.model_validate(item) for item in payload]
    return parser.parse(latest.read_bytes())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load injury snapshots into Postgres")
    parser.add_argument("--source", choices=["rotowire", "espn"], required=True)
    parser.add_argument("--since", help="Reserved for historical backfill scheduling")
    return parser


def _run_cli() -> None:
    args = _build_parser().parse_args()
    records = _load_records_from_raw(args.source)
    summary = InjuryLoader().load_snapshot(records)
    logger.info(
        "injury_load_complete",
        source=args.source,
        total_records=summary.total_records,
        inserted=summary.inserted,
        deduped=summary.deduped,
        unresolved_names=summary.unresolved_names,
        unmapped_statuses=summary.unmapped_statuses,
    )


if __name__ == "__main__":
    _run_cli()
