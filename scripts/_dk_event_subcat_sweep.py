"""
Sweep subcategory IDs on the event/eventSubcategory endpoint to find all O/U markets.
We know Points O/U = subcategoryId 12488.
"""

from __future__ import annotations

import asyncio
import argparse
import sys
from urllib.parse import quote

import httpx

_DEFAULT_EVENT_ID = "34109300"  # only used when args omitted (for local dev)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
MILESTONE_MT_IDS = {"12189","12190","12191","12192","12193","12194","12195","12196","12206","12197","12198","12199"}


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def count_player_ou(payload: dict) -> int:
    buckets: dict[str, set[str]] = {}
    selections = payload.get("selections") or []
    markets = payload.get("markets") or []
    for sel in (selections if isinstance(selections, list) else []):
        if not isinstance(sel, dict) or sel.get("milestoneValue") is not None: continue
        label = str(sel.get("label") or "")
        if label.endswith("+"): continue
        side = None
        for k in ("label", "name", "outcomeType"):
            t = str(sel.get(k) or "").lower()
            if "over" in t: side = "over"; break
            if "under" in t: side = "under"; break
        if not side: continue
        participant = ""
        for p in (sel.get("participants") or []):
            if isinstance(p, dict): participant = str(p.get("name") or "").strip(); break
        if not participant:
            for k in ("playerName","participantName"):
                v = str(sel.get(k) or "").strip()
                if v and " " in v: participant = v; break
        line = None
        for lk in ("points","line","handicap","value"):
            raw = sel.get(lk)
            if raw is not None:
                try: line = float(raw); break
                except: pass
        if participant and line is not None and " " in participant:
            buckets.setdefault(f"{participant}|{line}", set()).add(side)
    return sum(1 for s in buckets.values() if len(s) == 2)


def mt_names(payload: dict) -> list[str]:
    seen: set[str] = set(); out_list: list[str] = []
    for m in (payload.get("markets") or []):
        if not isinstance(m, dict): continue
        mt = m.get("marketType") or {}
        k = f"{mt.get('id')}:{mt.get('name')}"
        if k not in seen: seen.add(k); out_list.append(k)
    return out_list


def _build_headers(*, event_id: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://sportsbook.draftkings.com/event/nba-event/{event_id}",
        "User-Agent": _USER_AGENT,
    }


async def probe(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    *,
    event_id: str,
    sid: str,
    filter_name: str | None,
) -> tuple[str, int, list[str]] | None:
    async with sem:
        tv = quote(f"{event_id},{sid}", safe="")
        mq = quote(
            f"$filter=eventId eq '{event_id}' AND clientMetadata/subCategoryId eq '{sid}' AND tags/all(t: t ne 'SportcastBetBuilder')",
            safe="",
        )
        url = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "event/eventSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={tv}&marketsQuery={mq}&entity=markets"
        )
        try:
            r = await client.get(url, timeout=12)
            if r.status_code != 200: return None
            data = r.json()
            markets = data.get("markets") or []
            if not (isinstance(markets, list) and markets): return None
            ou = count_player_ou(data)
            names = mt_names(data)
            if filter_name:
                lowered = filter_name.lower()
                if not any(lowered in str(name).lower() for name in names):
                    return None
            flag = "  *** O/U ***" if ou > 0 else ""
            out(f"  {sid}: markets={len(markets)} ou_pairs={ou}{flag}  {names[:3]}")
            return (sid, ou, names)
        except Exception:
            return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep DraftKings eventSubcategory IDs for player O/U markets.")
    parser.add_argument("--event-id", required=True, help="DraftKings numeric event id, e.g. 34134193")
    parser.add_argument("--min-id", type=int, default=12000, help="Min subcategory id (inclusive)")
    parser.add_argument("--max-id", type=int, default=14000, help="Max subcategory id (exclusive)")
    parser.add_argument("--step", type=int, default=1, help="Step between subcategory ids")
    parser.add_argument("--concurrency", type=int, default=12, help="Concurrent requests")
    parser.add_argument(
        "--filter-name",
        default="",
        help='Optional substring filter on marketType names, e.g. "points + rebounds".',
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    event_id = str(args.event_id).strip()
    ids = [str(i) for i in range(int(args.min_id), int(args.max_id), int(args.step))]
    filter_name = str(args.filter_name).strip() or None

    out(f"Sweeping {len(ids)} subcategory IDs on event endpoint (event={event_id})...")

    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    async with httpx.AsyncClient(headers=_build_headers(event_id=event_id)) as client:
        results = list(
            await asyncio.gather(
                *[probe(client, sem, event_id=event_id, sid=sid, filter_name=filter_name) for sid in ids]
            )
        )

    found = [r for r in results if r is not None]
    found.sort(key=lambda r: (-r[1], -int(r[0])))

    out("\n=== RESULTS ===")
    for sid, ou, names in found:
        flag = "  <-- PLAYER O/U" if ou > 0 else ""
        out(f"  {sid}: ou_pairs={ou}{flag}  {names[:3]}")

    winners = [(sid, names) for sid, ou, names in found if ou > 0]
    if winners:
        ids_str = ",".join(sid for sid, _ in winners)
        out(f'\n>>> Player O/U subcategory IDs: {ids_str}')
        out("Market types found:")
        for sid, names in winners:
            out(f"  {sid}: {names[:3]}")
    else:
        out("\n>>> No player O/U found. Try wider range.")


if __name__ == "__main__":
    asyncio.run(main())
