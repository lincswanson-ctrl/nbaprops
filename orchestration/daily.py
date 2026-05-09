"""Daily-cycle helpers (minimal until Prefect/cron wiring is restored)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def run_pregame_edge_filter(
    game_date: str,
    *,
    data_root: Path = Path("data"),
    rules_path: Path = Path("config/filter_rules.yaml"),
    research_only_card: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run :mod:`scripts.filter_edges` logic in-process.

    Args:
        game_date: ``YYYY-MM-DD`` matching ``edges_<date>.json``.
        data_root: Repository ``data/`` directory.
        rules_path: Filter YAML path.
        research_only_card: When True, sets ``research_only`` on the output JSON envelope.
        dry_run: When True, do not write ``filtered_edges_<date>.json``.

    Returns:
        The ``filtered_edges`` payload dict (includes ``edges`` and ``summary``).
    """
    import json

    import yaml
    from engine.edge_filter import FilterRules, build_filtered_payload, filter_raw_edges

    in_path = data_root / "edges" / f"edges_{game_date}.json"
    out_path = data_root / "edges" / f"filtered_edges_{game_date}.json"
    if not in_path.exists():
        msg = f"Missing raw edges file: {in_path}"
        logger.error("pregame_filter_skipped", reason=msg)
        raise FileNotFoundError(msg)

    raw_data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict):
        raise ValueError(f"Expected YAML mapping in {rules_path}")
    rules = FilterRules.model_validate(raw_data)
    raw_edges = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(raw_edges, list):
        raise ValueError(f"Expected JSON array in {in_path}")
    records = [dict(x) for x in raw_edges if isinstance(x, dict)]

    edges, summary = filter_raw_edges(
        records,
        rules,
        game_date=game_date,
    )
    research_flag = research_only_card or any(
        str(x.get("market", "")).strip().lower() in set(rules.research_only_markets)
        for x in records
    )
    payload = build_filtered_payload(edges, summary, research_only=research_flag)
    logger.info(
        "pregame_filter_complete",
        date=game_date,
        kept=summary.kept_bettable,
        rejected=summary.rejected,
        dry_run=dry_run,
    )
    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
