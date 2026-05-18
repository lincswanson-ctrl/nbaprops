"""Discover DraftKings eventSubcategory IDs for combo player props.

This script probes DraftKings `event/eventSubcategory/v1/markets` for a single event id
and attempts to identify eventSubcategory IDs for combo markets (PRA, P+R, P+A).

It prints a single JSON object to stdout so that callers (e.g. PowerShell) can parse it
and set env vars for a subsequent scrape in the same process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

# Suppress httpx/httpcore INFO logs so only the final JSON goes to stdout.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from data_pipeline.scrapers.odds.draftkings import DraftKingsScraper


_COMBO_MARKET_PATTERNS: dict[str, tuple[str, ...]] = {
    "player_points_rebounds_assists": ("points", "rebounds", "assists"),
    "player_points_rebounds": ("points", "rebounds"),
    "player_points_assists": ("points", "assists"),
    "player_rebounds_assists": ("rebounds", "assists"),
}


@dataclass(frozen=True)
class DiscoveryResult:
    """Discovery outcome for one canonical combo market."""

    canonical_market: str
    event_subcategory_id: str
    ou_pairs: int
    market_type_name: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover DK combo eventSubcategory IDs for one event.")
    parser.add_argument("--event-id", required=True, help="DraftKings numeric event id, e.g. 34134193")
    parser.add_argument("--min-id", type=int, default=10000, help="Min subcategory id (inclusive)")
    parser.add_argument("--max-id", type=int, default=90000, help="Max subcategory id (exclusive)")
    parser.add_argument("--step", type=int, default=5, help="Step between subcategory ids")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent requests")
    return parser.parse_args()


def _count_player_ou_pairs(payload: dict[str, Any]) -> int:
    """Count player buckets that have both over and under selections."""
    buckets: dict[str, set[str]] = defaultdict(set)
    selections = payload.get("selections") or []
    if not isinstance(selections, list):
        return 0
    for sel in selections:
        if not isinstance(sel, dict):
            continue
        if sel.get("milestoneValue") is not None:
            continue
        label = str(sel.get("label") or "")
        if label.endswith("+"):
            continue
        side = None
        for key in ("label", "name", "outcomeType", "selectionType"):
            token = str(sel.get(key) or "").strip().lower()
            if token.startswith("over") or token in {"o"}:
                side = "over"
                break
            if token.startswith("under") or token in {"u"}:
                side = "under"
                break
        if side is None:
            continue
        participant = ""
        for p in sel.get("participants") or []:
            if isinstance(p, dict) and str(p.get("name") or "").strip():
                participant = str(p.get("name") or "").strip()
                break
        if not participant:
            participant = str(sel.get("participantName") or sel.get("playerName") or "").strip()
        if not participant or " " not in participant:
            continue
        line: float | None = None
        for lk in ("points", "line", "handicap", "value"):
            raw = sel.get(lk)
            if raw is None:
                continue
            try:
                line = float(raw)
                break
            except (TypeError, ValueError):
                continue
        if line is None:
            continue
        buckets[f"{participant}|{line}"].add(side)
    return sum(1 for sides in buckets.values() if len(sides) == 2)


def _market_type_names(payload: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for market in payload.get("markets") or []:
        if not isinstance(market, dict):
            continue
        mt = market.get("marketType") or {}
        if not isinstance(mt, dict):
            continue
        name = str(mt.get("name") or "").strip()
        if name:
            names.add(name)
    return sorted(names)


def _match_combo_market(market_type_name: str) -> str | None:
    lower = market_type_name.lower()
    if "team" in lower:
        return None
    for canonical, tokens in _COMBO_MARKET_PATTERNS.items():
        if all(token in lower for token in tokens):
            return canonical
    return None


async def discover_combo_subcategories_for_event(
    *,
    event_id: str,
    min_id: int,
    max_id: int,
    step: int,
    concurrency: int,
) -> dict[str, DiscoveryResult]:
    """Probe subcategory id range and return best match per canonical combo market."""
    event_id = str(event_id).strip()
    candidates = list(range(int(min_id), int(max_id), max(1, int(step))))
    best: dict[str, DiscoveryResult] = {}
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async with DraftKingsScraper() as dk:

        async def probe(sid: int) -> None:
            async with sem:
                try:
                    payload = await dk._fetch_event_subcat_payload(event_id, str(sid))
                except Exception:
                    return
                ou_pairs = _count_player_ou_pairs(payload)
                if ou_pairs <= 0:
                    return
                for name in _market_type_names(payload):
                    canonical = _match_combo_market(name)
                    if canonical is None:
                        continue
                    current = best.get(canonical)
                    result = DiscoveryResult(
                        canonical_market=canonical,
                        event_subcategory_id=str(sid),
                        ou_pairs=int(ou_pairs),
                        market_type_name=name,
                    )
                    if current is None or result.ou_pairs > current.ou_pairs:
                        best[canonical] = result

        await asyncio.gather(*(probe(sid) for sid in candidates))
    return best


def main() -> int:
    args = _parse_args()
    discovered = asyncio.run(
        discover_combo_subcategories_for_event(
            event_id=args.event_id,
            min_id=args.min_id,
            max_id=args.max_id,
            step=args.step,
            concurrency=args.concurrency,
        )
    )
    out: dict[str, Any] = {
        "event_id": str(args.event_id),
        "range": {"min_id": int(args.min_id), "max_id": int(args.max_id), "step": int(args.step)},
        "discovered": {
            key: {
                "event_subcategory_id": value.event_subcategory_id,
                "ou_pairs": value.ou_pairs,
                "market_type_name": value.market_type_name,
            }
            for key, value in sorted(discovered.items())
        },
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

