"""CLI: raw edges JSON -> filtered_edges JSON + summary (pregame filter step)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import structlog
import yaml
from pydantic import ValidationError

from engine.edge_filter import FilterRules, build_filtered_payload, filter_raw_edges

logger = structlog.get_logger(__name__)


def _load_raw_edges(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON array in {path}")
    return [dict(x) for x in payload if isinstance(x, dict)]


def _load_rules(path: Path) -> FilterRules:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return FilterRules.model_validate(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter raw edges into filtered_edges JSON.")
    parser.add_argument("--date", required=True, help="Game date YYYY-MM-DD (artifact stem).")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Root containing edges/ directory.",
    )
    parser.add_argument("--rules", type=Path, default=Path("config/filter_rules.yaml"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log summary only; do not write filtered_edges file.",
    )
    parser.add_argument(
        "--research-only-card",
        action="store_true",
        help="Set research_only=true on the output payload envelope.",
    )
    args = parser.parse_args()

    in_path = args.data_root / "edges" / f"edges_{args.date}.json"
    out_path = args.data_root / "edges" / f"filtered_edges_{args.date}.json"

    if not in_path.exists():
        logger.error("missing_input", path=str(in_path))
        return 2

    try:
        rules = _load_rules(args.rules)
    except (OSError, ValidationError, ValueError) as exc:
        logger.error("rules_load_failed", path=str(args.rules), error=str(exc))
        return 3

    raw = _load_raw_edges(in_path)
    edges, summary = filter_raw_edges(
        raw,
        rules,
        game_date=args.date,
    )
    research_flag = bool(args.research_only_card) or any(
        str(x.get("market", "")).strip().lower() in set(rules.research_only_markets) for x in raw
    )
    payload = build_filtered_payload(edges, summary, research_only=research_flag)

    logger.info(
        "filter_complete",
        date=args.date,
        raw=summary.raw_edges,
        kept=summary.kept_bettable,
        rejected=summary.rejected,
        deduped=summary.dedupe_losers,
        exposure_capped=summary.exposure_capped,
        novelty=summary.novelty_rejected,
        out_path=str(out_path),
    )

    if args.dry_run:
        print(json.dumps(payload["summary"], indent=2))
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
