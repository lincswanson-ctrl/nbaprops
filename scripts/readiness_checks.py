"""Hard-fail readiness checks for daily pipeline confidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.config import get_settings
from data_pipeline.db import get_connection
from data_pipeline.logging import get_logger
from scripts.validate_injury_sources import validate_sources

logger = get_logger(__name__)


@dataclass(frozen=True)
class CheckResult:
    """Outcome for one readiness check."""

    name: str
    passed: bool
    details: str


def _table_row_count(table: str) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS count FROM {table}")
            row = cur.fetchone()
    return int(row["count"]) if row else 0


def check_minimum_rows(
    *,
    min_games: int,
    min_players: int,
    min_player_game_stats: int,
    min_injury_rows: int,
) -> list[CheckResult]:
    """Check core table row counts against minimum thresholds."""

    counts = {
        "games": _table_row_count("games"),
        "players": _table_row_count("players"),
        "player_game_stats": _table_row_count("player_game_stats"),
        "injury_status": _table_row_count("injury_status"),
    }
    checks = [
        ("games", counts["games"], min_games),
        ("players", counts["players"], min_players),
        ("player_game_stats", counts["player_game_stats"], min_player_game_stats),
        ("injury_status", counts["injury_status"], min_injury_rows),
    ]
    return [
        CheckResult(
            name=f"{name}_minimum_rows",
            passed=count >= threshold,
            details=f"count={count} threshold={threshold}",
        )
        for name, count, threshold in checks
    ]


def check_recent_raw_files(max_age_minutes: int) -> list[CheckResult]:
    """Ensure recent raw snapshots exist for nba_stats and injuries."""

    now = datetime.now(timezone.utc)
    raw_root = get_settings().raw_storage_path
    checks: list[CheckResult] = []
    targets = {
        "nba_stats": raw_root / "nba_stats",
        "injuries_rotowire": raw_root / "injuries" / "rotowire",
        "injuries_espn": raw_root / "injuries" / "espn",
    }
    for name, folder in targets.items():
        latest = _latest_file(folder)
        if latest is None:
            checks.append(CheckResult(name=f"{name}_freshness", passed=False, details="no files found"))
            continue
        file_age = now - datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
        checks.append(
            CheckResult(
                name=f"{name}_freshness",
                passed=file_age <= timedelta(minutes=max_age_minutes),
                details=f"latest={latest.name} age_minutes={round(file_age.total_seconds() / 60.0, 2)}",
            )
        )
    return checks


def check_injury_disagreement(max_disagreement_pct: float) -> CheckResult:
    """Fail if Rotowire/ESPN disagreement exceeds threshold."""

    total, disagreements = validate_sources(datetime.now(timezone.utc))
    rate = (disagreements / total * 100.0) if total else 0.0
    return CheckResult(
        name="injury_source_disagreement",
        passed=rate < max_disagreement_pct,
        details=f"total={total} disagreements={disagreements} rate={round(rate, 2)} threshold={max_disagreement_pct}",
    )


def run_readiness_checks(
    *,
    min_games: int,
    min_players: int,
    min_player_game_stats: int,
    min_injury_rows: int,
    max_raw_age_minutes: int,
    max_injury_disagreement_pct: float,
) -> list[CheckResult]:
    """Run all readiness checks and return structured results."""

    results: list[CheckResult] = []
    results.extend(
        check_minimum_rows(
            min_games=min_games,
            min_players=min_players,
            min_player_game_stats=min_player_game_stats,
            min_injury_rows=min_injury_rows,
        )
    )
    results.extend(check_recent_raw_files(max_age_minutes=max_raw_age_minutes))
    results.append(check_injury_disagreement(max_disagreement_pct=max_injury_disagreement_pct))
    return results


def _latest_file(folder: Path) -> Path | None:
    if not folder.exists():
        return None
    files = [path for path in folder.rglob("*") if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def main() -> int:
    """Run readiness checks and return non-zero on any failure."""

    parser = argparse.ArgumentParser(description="Pipeline readiness checks")
    parser.add_argument("--min-games", type=int, default=1)
    parser.add_argument("--min-players", type=int, default=1)
    parser.add_argument("--min-player-game-stats", type=int, default=1)
    parser.add_argument("--min-injury-rows", type=int, default=1)
    parser.add_argument("--max-raw-age-minutes", type=int, default=180)
    parser.add_argument("--max-injury-disagreement-pct", type=float, default=5.0)
    args = parser.parse_args()

    results = run_readiness_checks(
        min_games=args.min_games,
        min_players=args.min_players,
        min_player_game_stats=args.min_player_game_stats,
        min_injury_rows=args.min_injury_rows,
        max_raw_age_minutes=args.max_raw_age_minutes,
        max_injury_disagreement_pct=args.max_injury_disagreement_pct,
    )
    failures = [result for result in results if not result.passed]
    for result in results:
        log_fn = logger.info if result.passed else logger.warning
        log_fn("readiness_check", name=result.name, passed=result.passed, details=result.details)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
