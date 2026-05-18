"""Resolve official names to canonical officials rows."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz, process

from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger

logger = get_logger(__name__)
_SUFFIX_RE = re.compile(r"\b(jr\.?|sr\.?|ii|iii|iv)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedOfficial:
    """Normalized official record used by loaders."""

    official_id: str
    full_name: str


def normalize_official_name(raw_name: str) -> str:
    """Normalize official names for deterministic matching."""

    stripped = _SUFFIX_RE.sub("", raw_name).replace(".", " ").replace("'", " ")
    return " ".join(stripped.lower().split())


def resolve_official_name(raw_name: str, nba_stats_person_id: int | None = None) -> ResolvedOfficial:
    """Resolve a raw name to existing official row or create one."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            return resolve_official_name_with_cursor(cur, raw_name, nba_stats_person_id=nba_stats_person_id)


def resolve_official_name_with_cursor(
    cur: Any,
    raw_name: str,
    nba_stats_person_id: int | None = None,
) -> ResolvedOfficial:
    """Resolve a raw official name using an existing DB cursor."""

    normalized = normalize_official_name(raw_name)
    existing = _fetch_officials(cur)
    match = _exact_match(existing, normalized)
    if match is None:
        match = _initialed_match(existing, normalized)
    if match is None:
        match = _fuzzy_match(existing, normalized)
    if match is not None:
        _maybe_backfill_stats_id(cur, match["official_id"], nba_stats_person_id)
        return ResolvedOfficial(official_id=str(match["official_id"]), full_name=str(match["full_name"]))

    created = _create_official(cur, raw_name, nba_stats_person_id)
    logger.warning("official_created_from_unmatched_name", raw_name=raw_name, official_id=created)
    return ResolvedOfficial(official_id=created, full_name=raw_name.strip())


def _fetch_officials(cur: Any) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT official_id, full_name
        FROM officials
        WHERE is_active = TRUE
        """
    )
    return list(cur.fetchall())


def _exact_match(rows: list[dict[str, Any]], normalized_name: str) -> dict[str, Any] | None:
    for row in rows:
        if normalize_official_name(str(row["full_name"])) == normalized_name:
            return row
    return None


def _initialed_match(rows: list[dict[str, Any]], normalized_name: str) -> dict[str, Any] | None:
    parts = normalized_name.split()
    if len(parts) != 2 or len(parts[0]) != 1:
        return None
    initial = parts[0]
    last = parts[1]
    candidates = []
    for row in rows:
        pieces = normalize_official_name(str(row["full_name"])).split()
        if len(pieces) < 2:
            continue
        if pieces[0].startswith(initial) and pieces[-1] == last:
            candidates.append(row)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _fuzzy_match(rows: list[dict[str, Any]], normalized_name: str) -> dict[str, Any] | None:
    if not rows:
        return None
    choices = {str(row["official_id"]): normalize_official_name(str(row["full_name"])) for row in rows}
    result = process.extractOne(normalized_name, choices, scorer=fuzz.ratio)
    if result is None:
        return None
    _match_str, score, official_key = result
    if score < 92:
        return None
    return next((row for row in rows if str(row["official_id"]) == str(official_key)), None)


def _maybe_backfill_stats_id(cur: Any, official_id: str, nba_stats_person_id: int | None) -> None:
    if nba_stats_person_id is None:
        return
    cur.execute(
        """
        UPDATE officials
        SET nba_stats_person_id = COALESCE(nba_stats_person_id, %(nba_stats_person_id)s)
        WHERE official_id = %(official_id)s
        """,
        {"official_id": official_id, "nba_stats_person_id": nba_stats_person_id},
    )


def _create_official(cur: Any, raw_name: str, nba_stats_person_id: int | None) -> str:
    cleaned = " ".join(raw_name.strip().split())
    parts = cleaned.split()
    first_name = parts[0] if parts else cleaned
    last_name = parts[-1] if len(parts) > 1 else ""
    official_id = str(nba_stats_person_id) if nba_stats_person_id is not None else f"gen_{uuid.uuid4().hex[:12]}"
    cur.execute(
        """
        INSERT INTO officials (
            official_id, first_name, last_name, full_name, nba_stats_person_id, is_active
        )
        VALUES (
            %(official_id)s, %(first_name)s, %(last_name)s, %(full_name)s, %(nba_stats_person_id)s, TRUE
        )
        ON CONFLICT (official_id) DO UPDATE SET
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            full_name = EXCLUDED.full_name,
            nba_stats_person_id = COALESCE(officials.nba_stats_person_id, EXCLUDED.nba_stats_person_id),
            is_active = TRUE
        """,
        {
            "official_id": official_id,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": cleaned,
            "nba_stats_person_id": nba_stats_person_id,
        },
    )
    return official_id
