"""Generate a spec-01/spec-02 readiness report with hard thresholds."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger
from scripts.validate_injury_sources import validate_sources

logger = get_logger(__name__)


@dataclass(frozen=True)
class Metric:
    """Named metric result."""

    name: str
    value: float
    threshold: float
    passed: bool


def _count(query: str, params: dict[str, object] | None = None) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or {})
            row = cur.fetchone()
            if row is None:
                return 0
            return int(next(iter(row.values())))


def _injury_resolution_rate() -> float:
    total = _count("SELECT COUNT(*) AS c FROM injury_status")
    unresolved = _count("SELECT COALESCE(SUM(occurrences), 0) AS c FROM unresolved_player_names")
    if total + unresolved == 0:
        return 0.0
    return (total / (total + unresolved)) * 100.0


def build_metrics(min_games: int, min_stats_rows: int, min_resolution_pct: float) -> list[Metric]:
    """Build core completion metrics aligned to spec success criteria."""

    game_count = _count("SELECT COUNT(*) AS c FROM games")
    stats_count = _count("SELECT COUNT(*) AS c FROM player_game_stats")
    resolution_pct = _injury_resolution_rate()
    shared_players, disagreements = validate_sources(datetime.now(timezone.utc))
    disagreement_pct = (disagreements / shared_players * 100.0) if shared_players else 0.0
    return [
        Metric("games_loaded", float(game_count), float(min_games), game_count >= min_games),
        Metric(
            "player_game_stats_loaded",
            float(stats_count),
            float(min_stats_rows),
            stats_count >= min_stats_rows,
        ),
        Metric(
            "injury_name_resolution_pct",
            round(resolution_pct, 3),
            min_resolution_pct,
            resolution_pct >= min_resolution_pct,
        ),
        Metric(
            "injury_source_disagreement_pct",
            round(disagreement_pct, 3),
            5.0,
            disagreement_pct < 5.0,
        ),
    ]


def main() -> int:
    """CLI entrypoint for readiness report."""

    parser = argparse.ArgumentParser(description="Spec readiness report")
    parser.add_argument("--min-games", type=int, default=10)
    parser.add_argument("--min-stats-rows", type=int, default=100)
    parser.add_argument("--min-resolution-pct", type=float, default=99.5)
    parser.add_argument("--output", help="Optional path to write JSON metrics report")
    args = parser.parse_args()

    metrics = build_metrics(
        min_games=args.min_games,
        min_stats_rows=args.min_stats_rows,
        min_resolution_pct=args.min_resolution_pct,
    )
    for metric in metrics:
        level = logger.info if metric.passed else logger.warning
        level(
            "spec_metric",
            name=metric.name,
            value=metric.value,
            threshold=metric.threshold,
            passed=metric.passed,
        )
    if args.output:
        payload = [
            {
                "name": metric.name,
                "value": metric.value,
                "threshold": metric.threshold,
                "passed": metric.passed,
            }
            for metric in metrics
        ]
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    return 0 if all(metric.passed for metric in metrics) else 1


if __name__ == "__main__":
    raise SystemExit(main())
