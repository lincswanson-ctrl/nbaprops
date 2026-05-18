"""Load prospective and retrospective official crews into Postgres."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from data_pipeline.config import get_settings
from data_pipeline.db import get_connection
from data_pipeline.loaders.official_resolution import resolve_official_name_with_cursor
from data_pipeline.logging import get_logger
from data_pipeline.scrapers.officials import AssignmentRow, NBAOfficialsScraper

logger = get_logger(__name__)


@dataclass(frozen=True)
class OfficialsLoadSummary:
    """Summary counters for officials load operations."""

    rows_seen: int
    rows_upserted: int
    late_changes: int


class OfficialsLoader:
    """Load officials from nba_official and box score source data."""

    def __init__(self) -> None:
        self._raw_root = get_settings().raw_storage_path

    def load_from_nba_official(self, target_date: date) -> OfficialsLoadSummary:
        """Load prospective assignments from official NBA HTML."""

        scraper = NBAOfficialsScraper()
        raw_files = sorted((self._raw_root / "officials" / "nba_official" / target_date.isoformat()).glob("*.html"))
        if not raw_files:
            raise FileNotFoundError("No raw officials HTML found; run scraper first.")
        latest = raw_files[-1]
        rows = scraper.parse_assignments(latest.read_bytes(), target_date)
        upserted = 0
        with get_connection() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    game_id = _resolve_game_id(cur, row.game_date, row.game_label)
                    if game_id is None:
                        continue
                    crew = _resolve_crew_ids(cur, row)
                    _upsert_game_officials(
                        cur=cur,
                        game_id=game_id,
                        crew=crew,
                        source="nba_official_prospective",
                        raw_response_path=str(latest),
                        announced_at=datetime.now(UTC),
                        confirmed_at=None,
                    )
                    upserted += 1
        return OfficialsLoadSummary(rows_seen=len(rows), rows_upserted=upserted, late_changes=0)

    def load_from_nba_stats(self, season: str) -> OfficialsLoadSummary:
        """Load retrospective officials from boxscoresummaryv2 raw files."""

        box_root = self._raw_root / "nba_stats" / "boxscoresummaryv2" / season
        files = sorted(box_root.glob("*.json"))
        late_changes = 0
        upserted = 0
        with get_connection() as conn:
            with conn.cursor() as cur:
                for raw_file in files:
                    payload = json.loads(raw_file.read_text(encoding="utf-8"))
                    crew = _extract_boxscore_officials(payload)
                    if not crew:
                        continue
                    game_id = _extract_game_id(payload) or raw_file.stem
                    resolved = _resolve_box_crew_ids(cur, crew)
                    prior = _existing_game_officials(cur, game_id)
                    _upsert_game_officials(
                        cur=cur,
                        game_id=game_id,
                        crew=resolved,
                        source="nba_stats_box_score",
                        raw_response_path=str(raw_file),
                        announced_at=prior.get("announced_at") if prior else None,
                        confirmed_at=datetime.now(UTC),
                    )
                    upserted += 1
                    if prior and _is_late_change(prior, resolved):
                        late_changes += _record_late_changes(cur, game_id, prior, resolved)
        return OfficialsLoadSummary(rows_seen=len(files), rows_upserted=upserted, late_changes=late_changes)


def _resolve_game_id(cur: Any, game_date: date, game_label: str) -> str | None:
    if "@" not in game_label:
        return None
    away_name, home_name = [part.strip() for part in game_label.split("@", maxsplit=1)]
    cur.execute(
        """
        SELECT g.game_id
        FROM games g
        JOIN teams home ON home.team_id = g.home_team_id
        JOIN teams away ON away.team_id = g.away_team_id
        WHERE g.date = %(game_date)s
          AND (
                lower(home.name) = lower(%(home_name)s)
             OR lower(home.abbreviation) = lower(%(home_name)s)
          )
          AND (
                lower(away.name) = lower(%(away_name)s)
             OR lower(away.abbreviation) = lower(%(away_name)s)
          )
        LIMIT 1
        """,
        {
            "game_date": game_date,
            "home_name": _canonical_team_label(home_name),
            "away_name": _canonical_team_label(away_name),
        },
    )
    row = cur.fetchone()
    return str(row["game_id"]) if row else None


def _resolve_crew_ids(cur: Any, row: AssignmentRow) -> dict[str, str | None]:
    return {
        "crew_chief_id": (
            resolve_official_name_with_cursor(cur, row.crew_chief).official_id if row.crew_chief else None
        ),
        "referee_id": resolve_official_name_with_cursor(cur, row.referee).official_id if row.referee else None,
        "umpire_id": resolve_official_name_with_cursor(cur, row.umpire).official_id if row.umpire else None,
    }


def _resolve_box_crew_ids(cur: Any, crew: list[dict[str, Any]]) -> dict[str, str | None]:
    role_keys = ["crew_chief_id", "referee_id", "umpire_id"]
    resolved: dict[str, str | None] = {k: None for k in role_keys}
    for index, official in enumerate(crew[:3]):
        full_name = f"{official.get('FIRST_NAME', '')} {official.get('LAST_NAME', '')}".strip()
        official_id = resolve_official_name_with_cursor(
            cur,
            full_name,
            nba_stats_person_id=_safe_int(official.get("OFFICIAL_ID")),
        ).official_id
        resolved[role_keys[index]] = official_id
    return resolved


def _canonical_team_label(raw: str) -> str:
    clean = " ".join(raw.strip().split())
    aliases = {
        "LA Clippers": "Los Angeles Clippers",
        "LA Lakers": "Los Angeles Lakers",
        "NY Knicks": "New York Knicks",
    }
    return aliases.get(clean, clean)


def _upsert_game_officials(
    *,
    cur: Any,
    game_id: str,
    crew: dict[str, str | None],
    source: str,
    raw_response_path: str,
    announced_at: datetime | None,
    confirmed_at: datetime | None,
) -> None:
    cur.execute(
        """
        INSERT INTO game_officials (
            game_id, crew_chief_id, referee_id, umpire_id, source,
            announced_at, confirmed_at, raw_response_path
        )
        VALUES (
            %(game_id)s, %(crew_chief_id)s, %(referee_id)s, %(umpire_id)s, %(source)s,
            %(announced_at)s, %(confirmed_at)s, %(raw_response_path)s
        )
        ON CONFLICT (game_id) DO UPDATE SET
            crew_chief_id = EXCLUDED.crew_chief_id,
            referee_id = EXCLUDED.referee_id,
            umpire_id = EXCLUDED.umpire_id,
            source = EXCLUDED.source,
            announced_at = COALESCE(game_officials.announced_at, EXCLUDED.announced_at),
            confirmed_at = COALESCE(EXCLUDED.confirmed_at, game_officials.confirmed_at),
            raw_response_path = EXCLUDED.raw_response_path,
            updated_at = NOW()
        """,
        {
            "game_id": game_id,
            "crew_chief_id": crew.get("crew_chief_id"),
            "referee_id": crew.get("referee_id"),
            "umpire_id": crew.get("umpire_id"),
            "source": source,
            "announced_at": announced_at,
            "confirmed_at": confirmed_at,
            "raw_response_path": raw_response_path,
        },
    )


def _existing_game_officials(cur: Any, game_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT game_id, crew_chief_id, referee_id, umpire_id, announced_at
        FROM game_officials
        WHERE game_id = %(game_id)s
        LIMIT 1
        """,
        {"game_id": game_id},
    )
    return cur.fetchone()


def _is_late_change(existing: dict[str, Any], actual: dict[str, str | None]) -> bool:
    for key in ("crew_chief_id", "referee_id", "umpire_id"):
        if existing.get(key) and actual.get(key) and str(existing[key]) != str(actual[key]):
            return True
    return False


def _record_late_changes(
    cur: Any,
    game_id: str,
    existing: dict[str, Any],
    actual: dict[str, str | None],
) -> int:
    changes = 0
    for role, key in (("crew_chief", "crew_chief_id"), ("referee", "referee_id"), ("umpire", "umpire_id")):
        announced = existing.get(key)
        confirmed = actual.get(key)
        if not announced or not confirmed or str(announced) == str(confirmed):
            continue
        cur.execute(
            """
            INSERT INTO official_assignment_changes (
                game_id, role, announced_official_id, actual_official_id
            )
            VALUES (%(game_id)s, %(role)s, %(announced)s, %(actual)s)
            """,
            {"game_id": game_id, "role": role, "announced": announced, "actual": confirmed},
        )
        changes += 1
    if changes:
        cur.execute(
            "UPDATE game_officials SET is_late_change = TRUE, updated_at = NOW() WHERE game_id = %(game_id)s",
            {"game_id": game_id},
        )
    return changes


def _extract_boxscore_officials(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result_sets = payload.get("resultSets", [])
    officials = next(
        (item for item in result_sets if isinstance(item, dict) and item.get("name") == "Officials"),
        None,
    )
    if officials is None:
        return []
    headers = officials.get("headers", [])
    idx = {name: i for i, name in enumerate(headers)}
    rows: list[dict[str, Any]] = []
    for row in officials.get("rowSet", []):
        rows.append(
            {
                "OFFICIAL_ID": row[idx["OFFICIAL_ID"]] if "OFFICIAL_ID" in idx else None,
                "FIRST_NAME": row[idx["FIRST_NAME"]] if "FIRST_NAME" in idx else "",
                "LAST_NAME": row[idx["LAST_NAME"]] if "LAST_NAME" in idx else "",
                "JERSEY_NUM": row[idx["JERSEY_NUM"]] if "JERSEY_NUM" in idx else None,
            }
        )
    return rows


def _extract_game_id(payload: dict[str, Any]) -> str | None:
    result_sets = payload.get("resultSets", [])
    game_summary = next(
        (item for item in result_sets if isinstance(item, dict) and item.get("name") == "GameSummary"),
        None,
    )
    if game_summary is None:
        return None
    headers = game_summary.get("headers", [])
    if "GAME_ID" not in headers:
        return None
    idx = headers.index("GAME_ID")
    rowset = game_summary.get("rowSet", [])
    if not rowset:
        return None
    return str(rowset[0][idx])


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load officials into Postgres")
    parser.add_argument("--source", choices=["nba_official", "nba_stats"], required=True)
    parser.add_argument("--date", help="Date for nba_official load (YYYY-MM-DD)")
    parser.add_argument("--season", help="Season label for nba_stats load, e.g. 2024-25")
    return parser


def _run_cli() -> None:
    args = _build_parser().parse_args()
    loader = OfficialsLoader()
    if args.source == "nba_official":
        target = date.fromisoformat(args.date) if args.date else date.today()
        summary = loader.load_from_nba_official(target)
    else:
        if not args.season:
            raise SystemExit("--season is required for --source nba_stats")
        summary = loader.load_from_nba_stats(args.season)
    logger.info(
        "officials_load_complete",
        source=args.source,
        rows_seen=summary.rows_seen,
        rows_upserted=summary.rows_upserted,
        late_changes=summary.late_changes,
    )


if __name__ == "__main__":
    _run_cli()
