"""Database-backed dashboard query helpers."""

from __future__ import annotations

from datetime import date
from typing import Any

from data_pipeline.db import get_connection


def get_picks(target_date: date) -> list[dict[str, Any]]:
    """Return active picks for the selected slate date."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.pick_id,
                    p.game_id,
                    p.player_id,
                    p.game_date,
                    p.market,
                    p.side,
                    p.line,
                    p.book,
                    p.price,
                    p.edge_pct,
                    p.confidence,
                    p.grade,
                    p.grade_reason,
                    p.status,
                    p.model_version,
                    p.created_at,
                    COALESCE(pl.name, p.player_id) AS player_name,
                    ht.abbreviation AS home_team,
                    at.abbreviation AS away_team
                FROM picks p
                JOIN games g ON g.game_id = p.game_id
                LEFT JOIN players pl ON pl.player_id::text = p.player_id::text
                JOIN teams ht ON ht.team_id = g.home_team_id
                JOIN teams at ON at.team_id = g.away_team_id
                WHERE p.game_date = %(target_date)s
                  AND p.status = 'active'
                ORDER BY p.edge_pct DESC
                """,
                {"target_date": target_date},
            )
            return [dict(row) for row in cur.fetchall()]


def get_edge_distribution(target_date: date) -> list[float]:
    """Return edge percentages for histogram/distribution cards."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.edge_pct
                FROM edges e
                JOIN games g ON g.game_id = e.game_id
                WHERE g.date = %(target_date)s
                """,
                {"target_date": target_date},
            )
            return [float(row["edge_pct"]) for row in cur.fetchall()]


def get_summary(target_date: date) -> dict[str, Any]:
    """Return top-line summary counts for dashboard headers."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM games WHERE date = %(d)s", {"d": target_date})
            game_count = int(cur.fetchone()["n"])
            cur.execute("SELECT COUNT(*) AS n FROM picks WHERE game_date = %(d)s AND status = 'active'", {"d": target_date})
            pick_count = int(cur.fetchone()["n"])
            cur.execute(
                """
                SELECT COALESCE(AVG(edge_pct), 0.0) AS avg_edge
                FROM picks
                WHERE game_date = %(d)s
                  AND status = 'active'
                """,
                {"d": target_date},
            )
            avg_edge = float(cur.fetchone()["avg_edge"] or 0.0)
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM odds_normalized onr
                JOIN games g ON g.game_id = onr.game_id
                WHERE g.date = %(d)s
                """,
                {"d": target_date},
            )
            market_count = int(cur.fetchone()["n"])
    return {
        "game_count": game_count,
        "market_count": market_count,
        "pick_count": pick_count,
        "avg_edge": avg_edge,
    }


def get_available_pick_dates(limit: int = 30) -> list[date]:
    """Return recent dates where picks exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT game_date
                FROM picks
                ORDER BY game_date DESC
                LIMIT %(limit)s
                """,
                {"limit": limit},
            )
            return [row["game_date"] for row in cur.fetchall()]


def get_pipeline_health_rows() -> list[dict[str, Any]]:
    """Return source freshness rows from scraper_run_log."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT bookmaker_key AS source, MAX(ended_at) AS last_loaded_at
                FROM scraper_run_log
                GROUP BY bookmaker_key
                ORDER BY bookmaker_key
                """
            )
            return [dict(row) for row in cur.fetchall()]


def get_dates_with_edges_or_picks(limit: int = 60) -> list[date]:
    """Return recent dates with either edges or picks."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT d FROM (
                    SELECT g.date AS d
                    FROM edges e
                    JOIN games g ON g.game_id = e.game_id
                    UNION
                    SELECT p.game_date AS d
                    FROM picks p
                ) u
                ORDER BY d DESC
                LIMIT %(limit)s
                """,
                {"limit": limit},
            )
            return [row["d"] for row in cur.fetchall()]


def get_edges(target_date: date) -> list[dict[str, Any]]:
    """Return all computed edges for the requested date."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    e.game_id,
                    e.market,
                    e.side,
                    e.model_prob,
                    e.market_prob,
                    e.edge_pct,
                    e.decision,
                    e.rejection_reason,
                    COALESCE(pl.name, e.player_id::text) AS player_name,
                    e.scored_at,
                    e.model_version,
                    ht.abbreviation AS home_team,
                    at.abbreviation AS away_team
                FROM edges e
                JOIN games g ON g.game_id = e.game_id
                LEFT JOIN players pl ON pl.player_id::text = e.player_id::text
                JOIN teams ht ON ht.team_id = g.home_team_id
                JOIN teams at ON at.team_id = g.away_team_id
                WHERE g.date = %(d)s
                ORDER BY e.edge_pct DESC
                """,
                {"d": target_date},
            )
            return [dict(row) for row in cur.fetchall()]


def get_rejected_edges(target_date: date, edge_threshold: float = 0.02) -> list[dict[str, Any]]:
    """Return edges that did not clear the pick threshold."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    e.game_id,
                    e.player_id,
                    e.market,
                    e.side,
                    e.model_prob,
                    e.market_prob,
                    e.edge_pct,
                    e.market_book AS book,
                    e.market_price AS price,
                    e.decision,
                    e.rejection_reason AS reason,
                    COALESCE(pl.name, e.player_id::text) AS player_name,
                    e.scored_at,
                    e.model_version
                FROM edges e
                JOIN games g ON g.game_id = e.game_id
                LEFT JOIN players pl ON pl.player_id::text = e.player_id::text
                WHERE g.date = %(d)s
                  AND e.decision = 'reject'
                ORDER BY e.edge_pct DESC
                """,
                {"d": target_date, "t": edge_threshold},
            )
            rows = [dict(row) for row in cur.fetchall()]
    return rows


def get_picks_by_date(target_date: date) -> list[dict[str, Any]]:
    """Alias for history pages to retrieve picks."""
    return get_picks(target_date)


def get_team_moneyline_rows(target_date: date) -> list[dict[str, Any]]:
    """Return moneyline picks for the selected date."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.game_id,
                    p.market,
                    p.side,
                    p.line,
                    p.edge_pct,
                    p.confidence,
                    p.model_version,
                    ht.abbreviation AS home_team,
                    at.abbreviation AS away_team
                FROM picks p
                JOIN games g ON g.game_id = p.game_id
                JOIN teams ht ON ht.team_id = g.home_team_id
                JOIN teams at ON at.team_id = g.away_team_id
                WHERE p.game_date = %(d)s
                  AND p.status = 'active'
                  AND p.market IN ('team_moneyline', 'moneyline')
                ORDER BY p.edge_pct DESC
                """,
                {"d": target_date},
            )
            return [dict(row) for row in cur.fetchall()]


def get_parlay_suggestions(target_date: date, limit: int = 8) -> list[dict[str, Any]]:
    """Build explainable pair-play suggestions from active picks."""
    picks = get_picks(target_date)
    if len(picks) < 2:
        return []
    ranked = sorted(picks, key=lambda r: float(r.get("edge_pct", 0.0)), reverse=True)
    suggestions: list[dict[str, Any]] = []
    used_pairs: set[tuple[int, int]] = set()
    for i, first in enumerate(ranked):
        for j, second in enumerate(ranked[i + 1 :], start=i + 1):
            if first.get("game_id") == second.get("game_id"):
                continue
            if str(first.get("player_id", "")) == str(second.get("player_id", "")):
                continue
            key = (i, j)
            if key in used_pairs:
                continue
            used_pairs.add(key)
            combined_edge = float(first.get("edge_pct", 0.0)) + float(second.get("edge_pct", 0.0))
            reasoning = (
                f"Cross-game diversification ({first.get('away_team')} @ {first.get('home_team')} + "
                f"{second.get('away_team')} @ {second.get('home_team')}); combined edge {combined_edge * 100:.1f}%."
            )
            suggestions.append(
                {
                    "leg_1": f"{first.get('player_name')} {first.get('market')} {first.get('side')} {first.get('line')} ({first.get('book')} {first.get('price')})",
                    "leg_2": f"{second.get('player_name')} {second.get('market')} {second.get('side')} {second.get('line')} ({second.get('book')} {second.get('price')})",
                    "combined_edge_pct": combined_edge,
                    "reasoning": reasoning,
                }
            )
            if len(suggestions) >= limit:
                return suggestions
    return suggestions
