"""Resolve scraped player names to players.player_id."""

from __future__ import annotations

import unicodedata
import re
from typing import Any

from rapidfuzz import fuzz, process

from data_pipeline.db import get_connection

_SUFFIX_RE = re.compile(r"\b(jr\.?|sr\.?|ii|iii|iv)\b", re.IGNORECASE)


def normalize_name(raw_name: str) -> str:
    """Normalize player name for deterministic matching."""

    no_diacritics = "".join(
        char for char in unicodedata.normalize("NFKD", raw_name) if not unicodedata.combining(char)
    )
    stripped = _SUFFIX_RE.sub("", no_diacritics.replace("'", " ").replace(".", " "))
    return " ".join(stripped.lower().split())


def resolve_player_name(raw_name: str, team_abbr: str | None = None) -> str | None:
    """Returns player_id or None if unresolved."""

    normalized = normalize_name(raw_name)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT player_id FROM player_name_aliases WHERE alias = %(alias)s",
                {"alias": normalized},
            )
            alias_row = cur.fetchone()
            if alias_row:
                return str(alias_row["player_id"])

            cur.execute(
                """
                SELECT p.player_id, p.name, t.abbreviation
                FROM players p
                LEFT JOIN teams t ON t.team_id = p.team_id
                """,
            )
            players = cur.fetchall()

            for player in players:
                if normalize_name(str(player["name"])) == normalized:
                    return str(player["player_id"])

            scoped = [p for p in players if team_abbr and p["abbreviation"] == team_abbr]
            scoped_match = _best_match(normalized, scoped, 90)
            if scoped_match:
                _remember_alias(cur, normalized, scoped_match["player_id"], "fuzzy_team")
                return str(scoped_match["player_id"])

            league_match = _best_match(normalized, players, 95)
            if league_match:
                _remember_alias(cur, normalized, league_match["player_id"], "fuzzy_league")
                return str(league_match["player_id"])

            suggested, confidence = _suggest_player(normalized, players)
            cur.execute(
                """
                INSERT INTO unresolved_player_names (
                    raw_name, source, team_hint, suggested_player_id, suggested_confidence
                ) VALUES (%(raw_name)s, 'injuries', %(team_hint)s, %(suggested_player_id)s, %(suggested_confidence)s)
                ON CONFLICT (raw_name, source) DO UPDATE SET
                    last_seen = NOW(),
                    occurrences = unresolved_player_names.occurrences + 1,
                    team_hint = EXCLUDED.team_hint,
                    suggested_player_id = EXCLUDED.suggested_player_id,
                    suggested_confidence = EXCLUDED.suggested_confidence
                """,
                {
                    "raw_name": raw_name,
                    "team_hint": team_abbr,
                    "suggested_player_id": suggested,
                    "suggested_confidence": confidence,
                },
            )
    return None


def _best_match(normalized: str, players: list[dict[str, Any]], threshold: int) -> dict[str, Any] | None:
    if not players:
        return None
    choices = {str(player["player_id"]): normalize_name(str(player["name"])) for player in players}
    result = process.extractOne(normalized, choices, scorer=fuzz.ratio)
    if not result:
        return None
    _matched_name, score, player_id = result
    if score < threshold:
        return None
    return next((player for player in players if str(player["player_id"]) == str(player_id)), None)


def _suggest_player(normalized: str, players: list[dict[str, Any]]) -> tuple[str | None, float | None]:
    if not players:
        return None, None
    choices = {str(player["player_id"]): normalize_name(str(player["name"])) for player in players}
    result = process.extractOne(normalized, choices, scorer=fuzz.ratio)
    if not result:
        return None, None
    _matched_name, score, player_id = result
    return str(player_id), float(score)


def _remember_alias(cur: Any, alias: str, player_id: str, source: str) -> None:
    cur.execute(
        """
        INSERT INTO player_name_aliases (alias, player_id, source, confirmed)
        VALUES (%(alias)s, %(player_id)s, %(source)s, FALSE)
        ON CONFLICT (alias) DO NOTHING
        """,
        {"alias": alias, "player_id": player_id, "source": source},
    )
