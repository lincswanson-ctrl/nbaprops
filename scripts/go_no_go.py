"""One-command go/no-go runner for Specs 01 and 02."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
import socket
from time import perf_counter
from urllib.parse import urlparse

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.config import get_settings
from data_pipeline.seasons import current_nba_season, previous_season


def _configure_stdio_utf8() -> None:
    """Avoid UnicodeEncodeError on Windows consoles when printing progress."""

    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except (OSError, ValueError, TypeError, AttributeError):
                pass


@dataclass(frozen=True)
class StepResult:
    """Execution result for a pipeline command."""

    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def _run(
    name: str,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> StepResult:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=300,
            env=env,
            cwd=str(cwd or REPO_ROOT),
        )  # noqa: S603
        return StepResult(
            name=name,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        return StepResult(
            name=name,
            command=command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=f"Timed out after 300s: {' '.join(command)}",
        )


def _run_sql_file(name: str, database_url: str, sql_path: Path) -> StepResult:
    """Run one SQL migration file using psycopg."""

    full_sql_path = sql_path if sql_path.is_absolute() else REPO_ROOT / sql_path
    try:
        started = perf_counter()
        sql_text = full_sql_path.read_text(encoding="utf-8")
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(sql_text)
            conn.commit()
        elapsed = round(perf_counter() - started, 2)
        return StepResult(
            name=name,
            command=["psycopg", str(full_sql_path)],
            returncode=0,
            stdout=f"Applied {full_sql_path} in {elapsed}s",
            stderr="",
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            name=name,
            command=["psycopg", str(full_sql_path)],
            returncode=1,
            stdout="",
            stderr=str(exc),
        )


def _tcp_probe(host: str, port: int, connect_timeout: float) -> None:
    """Open a TCP socket; raises OSError on failure."""

    with socket.create_connection((host, port), timeout=connect_timeout):
        pass


def _db_socket_reachable(
    database_url: str,
    *,
    connect_timeout: float = 2.0,
    overall_timeout: float = 6.0,
) -> tuple[bool, str]:
    """TCP reachability check with a hard wall-clock cap (DNS can hang otherwise)."""

    parsed = urlparse(database_url)
    host = parsed.hostname
    port = parsed.port or 5432
    if not host:
        return False, "DATABASE_URL missing hostname"

    result_slot: list[tuple[bool, str] | None] = [None]

    def _worker() -> None:
        try:
            _tcp_probe(host, port, connect_timeout)
            result_slot[0] = (True, f"reachable {host}:{port}")
        except OSError as exc:
            result_slot[0] = (False, f"unreachable {host}:{port} ({exc})")

    thread = threading.Thread(target=_worker, name="go_no_go_db_probe", daemon=True)
    thread.start()
    thread.join(timeout=overall_timeout)
    if thread.is_alive():
        return False, (
            f"unreachable {host}:{port} (no response within {overall_timeout}s; "
            "DNS or routing may be stalled - verify DATABASE_URL, VPN, or "
            "run with --skip-socket-preflight)"
        )
    if result_slot[0] is None:
        return False, f"unreachable {host}:{port} (probe finished without result)"
    return result_slot[0]


def main() -> int:
    """Execute full go/no-go checklist and write JSON artifact."""

    _configure_stdio_utf8()

    parser = argparse.ArgumentParser(description="Go/No-Go runner for specs 01/02")
    parser.add_argument("--season", default=current_nba_season())
    parser.add_argument("--output", default="go_no_go_report.json")
    parser.add_argument("--no-fail-fast", action="store_true")
    parser.add_argument(
        "--skip-socket-preflight",
        action="store_true",
        help="Skip TCP probe (use when DNS stalls); first DATABASE_URL is used; migrations still validate.",
    )
    parser.add_argument(
        "--db-preflight-timeout",
        type=float,
        default=6.0,
        metavar="SEC",
        help="Max wall-clock seconds per URL for DB host reachability (default: 6).",
    )
    args = parser.parse_args()
    fail_fast = not args.no_fail_fast
    settings = get_settings()
    database_url = os.getenv("DATABASE_URL") or settings.database_url
    transaction_url = os.getenv("DATABASE_URL_TRANSACTION") or settings.database_url_transaction
    if not database_url and not transaction_url:
        missing = {
            "all_passed": False,
            "results": [
                {
                    "name": "preflight_database_url",
                    "command": [],
                    "returncode": 1,
                    "passed": False,
                    "stdout": "",
                    "stderr": "Set DATABASE_URL or DATABASE_URL_TRANSACTION before running go/no-go.",
                }
            ],
        }
        Path(args.output).write_text(json.dumps(missing, indent=2), encoding="utf-8")
        return 1
    # Preserve order; drop duplicates so we do not probe the same DSN twice.
    candidate_urls = list(dict.fromkeys([url for url in (database_url, transaction_url) if url]))
    selected_url: str | None = None
    reachability_errors: list[str] = []
    print("go_no_go: checking database reachability...", file=sys.stderr)
    if args.skip_socket_preflight:
        selected_url = candidate_urls[0]
        sp = urlparse(selected_url)
        print(
            "go_no_go: --skip-socket-preflight (migrations will fail fast if host is down).",
            f"host={sp.hostname!s} port={sp.port or 5432}",
            file=sys.stderr,
        )
    else:
        for url in candidate_urls:
            sp = urlparse(url)
            print(
                f"go_no_go: probing {sp.hostname!s}:{sp.port or 5432} (max {args.db_preflight_timeout:g}s) ...",
                file=sys.stderr,
            )
            reachable, reachability_msg = _db_socket_reachable(
                url,
                overall_timeout=float(args.db_preflight_timeout),
            )
            if reachable:
                selected_url = url
                print(f"go_no_go: {reachability_msg}", file=sys.stderr)
                break
            reachability_errors.append(reachability_msg)
            print(f"go_no_go: {reachability_msg}", file=sys.stderr)
    if selected_url is not None:
        picked = urlparse(selected_url)
        print(
            "go_no_go: using this connection for migrations and pipeline steps ->",
            f"host={picked.hostname!s} port={picked.port or 5432}",
            file=sys.stderr,
        )
    if selected_url is None:
        payload = {
            "all_passed": False,
            "results": [
                {
                    "name": "preflight_database_socket",
                    "command": [],
                    "returncode": 1,
                    "passed": False,
                    "stdout": "",
                    "stderr": " ; ".join(reachability_errors),
                }
            ],
        }
        Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 1
    database_url = selected_url
    python_exe = sys.executable
    run_env = os.environ.copy()
    # Force all subprocesses to use the same DB endpoint that passed preflight.
    run_env["DATABASE_URL"] = database_url
    run_env["DATABASE_URL_TRANSACTION"] = database_url

    if database_url and ("pooler" in database_url or ":6543" in database_url):
        print(
            "go_no_go: note: Supabase pooler (6543) uses transaction pooling - "
            "if migrations fail, switch DATABASE_URL to the direct DB URI (port 5432) "
            "from Project Settings -> Database.",
            file=sys.stderr,
        )

    results: list[StepResult] = []
    print("go_no_go: applying SQL migrations...", file=sys.stderr)
    for mig_name, mig_rel in (
        ("migrate_001", Path("data_pipeline/schemas/001_initial_nba.sql")),
        ("migrate_002", Path("data_pipeline/schemas/002_injuries.sql")),
    ):
        print(f"go_no_go: step {mig_name} ...", file=sys.stderr)
        mig_result = _run_sql_file(mig_name, database_url, mig_rel)
        results.append(mig_result)
        print(
            f"go_no_go: step {mig_name} -> {'ok' if mig_result.passed else 'FAILED'}",
            file=sys.stderr,
        )

    steps = [
        (
            "scrape_nba",
            [
                python_exe,
                "-m",
                "data_pipeline.scrapers.nba_stats",
                "--season",
                args.season,
                "--endpoint",
                "core",
                "--max-games",
                "8",
                "--core-only",
            ],
        ),
        (
            "load_nba",
            [python_exe, "-m", "data_pipeline.loaders.nba_stats", "--season", args.season],
        ),
        ("validate_nba", [python_exe, "scripts/validate_nba_load.py"]),
        (
            "validate_bref",
            [
                python_exe,
                "scripts/validate_nba_vs_bref.py",
                "--season",
                previous_season(args.season),
            ],
        ),
        (
            "scrape_injuries",
            [python_exe, "-m", "data_pipeline.scrapers.injuries", "--source", "all", "--once"],
        ),
        (
            "load_injury_rw",
            [python_exe, "-m", "data_pipeline.loaders.injuries", "--source", "rotowire"],
        ),
        (
            "load_injury_es",
            [python_exe, "-m", "data_pipeline.loaders.injuries", "--source", "espn"],
        ),
        (
            "validate_injuries",
            [python_exe, "scripts/validate_injury_sources.py", "--min-shared-players", "20"],
        ),
        (
            "readiness",
            [
                python_exe,
                "scripts/readiness_checks.py",
                "--min-games",
                "1000",
                "--min-players",
                "450",
                "--min-player-game-stats",
                "50000",
                "--min-injury-rows",
                "50",
                "--max-raw-age-minutes",
                "180",
                "--max-injury-disagreement-pct",
                "100",
            ],
        ),
        ("tests", [python_exe, "-m", "pytest", "-q"]),
    ]

    for name, command in steps:
        print(f"go_no_go: step {name} ...", file=sys.stderr)
        result = _run(name, command, env=run_env)
        results.append(result)
        print(f"go_no_go: step {name} -> {'ok' if result.passed else 'FAILED'}", file=sys.stderr)
        if fail_fast and not result.passed:
            break
    payload = {
        "all_passed": all(result.passed for result in results),
        "results": [
            {
                "name": result.name,
                "command": result.command,
                "returncode": result.returncode,
                "passed": result.passed,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            }
            for result in results
        ],
    }
    output_path = Path(args.output)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"go_no_go: finished - wrote {output_path.resolve()} "
        f"(all_passed={payload['all_passed']})",
        file=sys.stderr,
    )
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("go_no_go: interrupted", file=sys.stderr)
        raise SystemExit(130) from None
