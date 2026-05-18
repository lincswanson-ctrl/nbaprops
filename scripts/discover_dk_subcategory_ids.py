"""
Discovery script: probe DraftKings leagueSubcategory IDs to find classic O/U player props.

Run from repo root:
    py -3 scripts/discover_dk_subcategory_ids.py

Outputs a ranked table of subcategory IDs that returned real Over/Under pairs
(non-milestone) so you can pick the right one for DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS.
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

_NBA_LEAGUE_ID = "42648"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://sportsbook.draftkings.com/leagues/basketball/nba",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}

# Subcategory IDs to probe.  Start with all known ones plus a sweep of nearby ranges.
_KNOWN = [
    "583", "584", "585", "586",          # combo markets
    "12195", "12196", "12197",            # known per-stat tabs
    "16477",                              # known: milestones
    "99901", "99902", "99903", "99904",  # steals/blocks/etc
]

# Additional sweep ranges (inclusive start, exclusive stop, step)
_SWEEP_RANGES: list[tuple[int, int, int]] = [
    (12180, 12215, 1),   # around the known milestones cluster
    (16400, 16500, 5),   # neighbourhood of 16477
    (16500, 16700, 5),
    (92000, 92020, 1),
    (100, 120, 1),       # low-ID classic markets sometimes live here
]


def _candidate_ids() -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for s in _KNOWN:
        if s not in seen:
            seen.add(s)
            ids.append(s)
    for start, stop, step in _SWEEP_RANGES:
        for i in range(start, stop, step):
            s = str(i)
            if s not in seen:
                seen.add(s)
                ids.append(s)
    return ids


@dataclass
class ProbeResult:
    subcategory_id: str
    http_status: int = 0
    events_count: int = 0
    markets_count: int = 0
    selections_count: int = 0
    milestone_markets: int = 0
    classic_ou_pairs: int = 0
    market_type_names: list[str] = field(default_factory=list)
    error: str = ""


def _is_milestone_market(market: dict) -> bool:
    mt = market.get("marketType") or {}
    if not isinstance(mt, dict):
        return False
    name = str(mt.get("name") or "").lower()
    mid = str(mt.get("id") or "")
    if "milestone" in name:
        return True
    if mid in {"12189", "12190", "12191", "12192", "12193", "12194", "12195", "12196", "12206"}:
        return True
    return False


def _selection_is_milestone(sel: dict) -> bool:
    if sel.get("milestoneValue") is not None:
        return True
    label = str(sel.get("label") or "").strip()
    return bool(label) and label.endswith("+")


def _extract_side(sel: dict) -> str | None:
    for key in ("label", "name", "outcomeType", "selectionType"):
        text = str(sel.get(key) or "").strip().lower()
        if "over" in text:
            return "over"
        if "under" in text:
            return "under"
    return None


def _count_classic_ou_pairs(payload: dict) -> int:
    """Count player-line buckets that have both Over and Under selections (non-milestone)."""
    buckets: dict[str, set[str]] = {}
    for market in payload.get("markets") or []:
        if not isinstance(market, dict):
            continue
        if _is_milestone_market(market):
            continue
        for key in ("selections", "outcomes", "options"):
            for sel in market.get(key) or []:
                if not isinstance(sel, dict):
                    continue
                if _selection_is_milestone(sel):
                    continue
                side = _extract_side(sel)
                if side is None:
                    continue
                # Try to get a participant name
                participant = ""
                for p in (sel.get("participants") or []):
                    if isinstance(p, dict):
                        participant = str(p.get("name") or "").strip()
                        if participant:
                            break
                if not participant:
                    for k in ("playerName", "participantName", "runnerName"):
                        participant = str(sel.get(k) or "").strip()
                        if participant and participant.lower() not in {"over", "under"}:
                            break
                # Get line
                line: float | None = None
                for lk in ("points", "line", "handicap", "value"):
                    raw = sel.get(lk)
                    if raw is not None:
                        try:
                            line = float(raw)
                            break
                        except (TypeError, ValueError):
                            pass
                if line is None:
                    label = str(sel.get("label") or "")
                    m = re.search(r"(-?\d+(?:\.\d+)?)", label)
                    if m:
                        line = float(m.group(1))
                if not participant or line is None:
                    continue
                key_str = f"{participant}|{line}"
                buckets.setdefault(key_str, set()).add(side)

    # Also scan top-level selections
    for sel in payload.get("selections") or []:
        if not isinstance(sel, dict) or _selection_is_milestone(sel):
            continue
        side = _extract_side(sel)
        if side is None:
            continue
        participant = ""
        for p in (sel.get("participants") or []):
            if isinstance(p, dict):
                participant = str(p.get("name") or "").strip()
                if participant:
                    break
        if not participant:
            for k in ("playerName", "participantName", "runnerName"):
                participant = str(sel.get(k) or "").strip()
                if participant and participant.lower() not in {"over", "under"}:
                    break
        line = None
        for lk in ("points", "line", "handicap", "value"):
            raw = sel.get(lk)
            if raw is not None:
                try:
                    line = float(raw)
                    break
                except (TypeError, ValueError):
                    pass
        if not participant or line is None:
            continue
        buckets.setdefault(f"{participant}|{line}", set()).add(side)

    return sum(1 for sides in buckets.values() if len(sides) == 2)


def _market_type_names(payload: dict) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for market in payload.get("markets") or []:
        if not isinstance(market, dict):
            continue
        mt = market.get("marketType") or {}
        if not isinstance(mt, dict):
            continue
        name = str(mt.get("name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


async def probe_one(client: httpx.AsyncClient, subcategory_id: str) -> ProbeResult:
    result = ProbeResult(subcategory_id=subcategory_id)
    try:
        events_query = quote(
            f"$filter=leagueId eq '{_NBA_LEAGUE_ID}' AND clientMetadata/Subcategories/any(s: s/Id eq '{subcategory_id}')",
            safe="",
        )
        markets_query = quote(
            f"$filter=clientMetadata/subCategoryId eq '{subcategory_id}' AND tags/all(t: t ne 'SportcastBetBuilder')",
            safe="",
        )
        include = quote("Events,Markets,Selections", safe=",")
        url = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "league/leagueSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={_NBA_LEAGUE_ID}&eventsQuery={events_query}"
            f"&marketsQuery={markets_query}&include={include}&entity=markets"
        )
        response = await client.get(url, timeout=20.0)
        result.http_status = response.status_code
        if response.status_code != 200:
            result.error = f"HTTP {response.status_code}"
            return result
        payload = response.json()
        events = payload.get("events") or []
        markets = payload.get("markets") or []
        selections = payload.get("selections") or []
        result.events_count = len(events) if isinstance(events, list) else 0
        result.markets_count = len(markets) if isinstance(markets, list) else 0
        result.selections_count = len(selections) if isinstance(selections, list) else 0
        result.milestone_markets = sum(
            1 for m in (markets if isinstance(markets, list) else [])
            if isinstance(m, dict) and _is_milestone_market(m)
        )
        result.classic_ou_pairs = _count_classic_ou_pairs(payload)
        result.market_type_names = _market_type_names(payload)
    except Exception as exc:
        result.error = str(exc)[:120]
    return result


async def main() -> None:
    ids = _candidate_ids()
    print(f"Probing {len(ids)} subcategory IDs against DraftKings NBA endpoint…\n")

    results: list[ProbeResult] = []
    semaphore = asyncio.Semaphore(5)  # max 5 concurrent requests

    async def bounded_probe(client: httpx.AsyncClient, sid: str) -> ProbeResult:
        async with semaphore:
            r = await probe_one(client, sid)
            status_str = (
                f"✓ {r.classic_ou_pairs} pairs" if r.classic_ou_pairs > 0
                else f"✗ milestones={r.milestone_markets} mkts={r.markets_count}"
                if r.http_status == 200
                else f"  {r.error or f'HTTP {r.http_status}'}"
            )
            print(f"  subcategory {sid:>7}: {status_str}")
            return r

    async with httpx.AsyncClient(headers=_HEADERS) as client:
        tasks = [bounded_probe(client, sid) for sid in ids]
        results = list(await asyncio.gather(*tasks))

    # Sort: classic O/U pairs descending, then events
    results.sort(key=lambda r: (-r.classic_ou_pairs, -r.events_count))

    print("\n" + "=" * 72)
    print("RESULTS — sorted by classic O/U pairs (highest first)")
    print("=" * 72)
    print(f"{'ID':>8}  {'status':>6}  {'events':>6}  {'markets':>7}  {'milestone':>9}  {'ou_pairs':>8}  market_type_names")
    print("-" * 100)
    for r in results:
        if r.http_status not in (200, 0):
            continue
        if r.markets_count == 0 and r.classic_ou_pairs == 0:
            continue
        names_str = ", ".join(r.market_type_names[:5])
        print(
            f"{r.subcategory_id:>8}  {r.http_status:>6}  {r.events_count:>6}  "
            f"{r.markets_count:>7}  {r.milestone_markets:>9}  {r.classic_ou_pairs:>8}  {names_str}"
        )

    print()
    winners = [r for r in results if r.classic_ou_pairs > 0]
    if winners:
        best_ids = ",".join(r.subcategory_id for r in winners)
        print(f"Set this env var to use the best classic O/U subcategories:")
        print(f'  $env:DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS="{best_ids}"')
    else:
        print("No subcategory returned classic Over/Under pairs today.")
        print("DraftKings may not have player props posted yet for today's games,")
        print("or the classic O/U subcategory ID is outside the probed range.")
        print("Try running again closer to game time, or expand _SWEEP_RANGES.")


if __name__ == "__main__":
    asyncio.run(main())
