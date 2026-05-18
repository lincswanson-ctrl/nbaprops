"""Single-entry pregame pipeline runner."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Callable

import structlog

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestration.pregame import (
    StepResult,
    run_edge_selection,
    run_features,
    run_ingest,
    run_model,
    run_odds,
    run_summary,
    run_validation,
)

logger = structlog.get_logger("pregame")

StepFn = Callable[[date, str], StepResult]


def _build_steps(*, allow_empty_odds: bool, books: tuple[str, ...]) -> list[tuple[str, StepFn]]:
    """Build ordered pipeline steps with runtime flags bound."""

    def _run_odds(target_date: date, season: str) -> StepResult:
        return run_odds(target_date, season, allow_empty_odds=allow_empty_odds, books=books)

    return [
        ("1_ingest", run_ingest),
        ("2_odds", _run_odds),
        ("3_features", run_features),
        ("4_model", run_model),
        ("5_edges", run_edge_selection),
        ("6_validate", run_validation),
        ("7_summary", run_summary),
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run end-to-end pregame build.")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or 'today'.")
    parser.add_argument("--season", required=True, help="Season label, e.g. 2025-26.")
    parser.add_argument(
        "--books",
        default="all",
        help="Comma-separated sportsbook keys: draftkings,fanduel,betmgm or all.",
    )
    parser.add_argument("--open-dashboard", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--allow-empty-odds",
        action="store_true",
        help="Continue in schedule-only mode when games exist but no odds are ingested.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print steps only.")
    return parser.parse_args()


def _resolve_date(raw_date: str) -> date:
    if raw_date == "today":
        return date.today()
    return date.fromisoformat(raw_date)


def _parse_books(raw_books: str) -> tuple[str, ...]:
    allowed = {"all", "draftkings", "fanduel", "betmgm"}
    tokens = tuple(token.strip().lower() for token in raw_books.split(",") if token.strip())
    if not tokens:
        raise ValueError("At least one book must be provided.")
    for token in tokens:
        if token not in allowed:
            raise ValueError(f"Unknown book '{token}'. Expected one of: all, draftkings, fanduel, betmgm.")
    if "all" in tokens:
        return ("all",)
    deduped = tuple(dict.fromkeys(tokens))
    return deduped


def _launch_dashboard() -> None:
    script = Path("scripts") / "run_dashboard.py"
    cmd = [sys.executable, str(script)]
    raise SystemExit(subprocess.call(cmd))


def main() -> None:
    args = _parse_args()
    target_date = _resolve_date(args.date)
    books = _parse_books(args.books)
    steps = _build_steps(allow_empty_odds=args.allow_empty_odds, books=books)
    completed: dict[str, dict[str, object]] = {}
    if args.dry_run:
        logger.info("pregame_dry_run", date=target_date.isoformat(), season=args.season, steps=[s[0] for s in steps])
        return

    for step_name, step_fn in steps:
        logger.info("pregame_step_start", step=step_name, date=target_date.isoformat())
        try:
            result = step_fn(target_date, args.season)
            completed[step_name] = dict(result.details)
            logger.info("pregame_step_complete", step=step_name, details=result.details)
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.error("pregame_step_failed", step=step_name, error=str(exc))
            if args.fail_fast:
                raise SystemExit(1) from exc

    _log_summary_block(target_date, completed)
    logger.info("pregame_complete", date=target_date.isoformat(), season=args.season)
    if args.open_dashboard:
        _launch_dashboard()


def _log_summary_block(target_date: date, completed: dict[str, dict[str, object]]) -> None:
    summary = completed.get("7_summary", {})
    edges = completed.get("5_edges", {})
    lines = [
        "========================================",
        f"PREGAME SUMMARY - {target_date.isoformat()}",
        "========================================",
        f"Games:   {summary.get('games', 0)}",
        f"Markets: {summary.get('markets', 0)}",
        f"Edges:   {summary.get('edges', 0)}",
        f"Picks:   {summary.get('picks', 0)}",
        f"Top edges tracked: {len(list(edges.get('top_10', [])))}",
        "========================================",
    ]
    logger.info("pregame_summary_block", summary_text="\n".join(lines))


if __name__ == "__main__":
    main()
