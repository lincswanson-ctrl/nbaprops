"""Dump DK payload from subcategory 16477 and show ALL market types + sample selections."""
import asyncio
import json
import sys
from urllib.parse import quote

import httpx

NBA_LEAGUE_ID = "42648"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://sportsbook.draftkings.com/leagues/basketball/nba",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


async def fetch(client: httpx.AsyncClient, sid: str) -> dict:
    eq = quote(
        f"$filter=leagueId eq '{NBA_LEAGUE_ID}' AND clientMetadata/Subcategories/any(s: s/Id eq '{sid}')",
        safe="",
    )
    mq = quote(
        f"$filter=clientMetadata/subCategoryId eq '{sid}' AND tags/all(t: t ne 'SportcastBetBuilder')",
        safe="",
    )
    url = (
        "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
        "league/leagueSubcategory/v1/markets"
        f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={eq}"
        f"&marketsQuery={mq}&include=Events&entity=events"
    )
    r = await client.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


async def main() -> None:
    async with httpx.AsyncClient(headers=HEADERS) as client:
        payload = await fetch(client, "16477")

    # Save full payload
    out_path = "data/raw/dk_16477_dump.json"
    import os; os.makedirs("data/raw", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    out(f"Saved full payload to {out_path}")
    out("")

    # Top-level keys
    out(f"Top-level keys: {list(payload.keys())}")
    out("")

    events = payload.get("events") or []
    markets = payload.get("markets") or []
    selections = payload.get("selections") or []
    out(f"events: {len(events) if isinstance(events, list) else type(events).__name__}")
    out(f"markets: {len(markets) if isinstance(markets, list) else type(markets).__name__}")
    out(f"selections (top-level): {len(selections) if isinstance(selections, list) else type(selections).__name__}")
    out("")

    # All market type IDs/names
    out("=== ALL market types in payload ===")
    mt_counts: dict[str, int] = {}
    for m in (markets if isinstance(markets, list) else []):
        if not isinstance(m, dict):
            continue
        mt = m.get("marketType") or {}
        mid = str(mt.get("id") or "?")
        mname = str(mt.get("name") or "?")
        key = f"{mid}:{mname}"
        mt_counts[key] = mt_counts.get(key, 0) + 1
    for k, v in sorted(mt_counts.items(), key=lambda x: -x[1]):
        out(f"  {v:>4}x  {k}")
    out("")

    # Sample 3 markets of each type — show all keys
    shown: set[str] = set()
    out("=== Sample market rows (first 2 per marketType) ===")
    for m in (markets if isinstance(markets, list) else []):
        if not isinstance(m, dict):
            continue
        mt = m.get("marketType") or {}
        key = f"{mt.get('id')}:{mt.get('name')}"
        if mt_counts.get(key, 0) > 0 and key not in shown:
            shown.add(key)
            out(f"\n  marketType: {key}")
            out(f"  market keys: {sorted(m.keys())}")
            out(f"  market name: {m.get('name')}")
            out(f"  subcategoryId: {m.get('subcategoryId')}")
            out(f"  tags: {m.get('tags')}")
            # Inner selections
            for sel_key in ("selections", "outcomes", "options"):
                inner = m.get(sel_key)
                if isinstance(inner, list) and inner:
                    out(f"  inner {sel_key} count: {len(inner)}")
                    s0 = inner[0]
                    if isinstance(s0, dict):
                        out(f"  inner sel[0] keys: {sorted(s0.keys())}")
                        out(f"  inner sel[0] label: {s0.get('label')}")
                        out(f"  inner sel[0] milestoneValue: {s0.get('milestoneValue')}")
                        out(f"  inner sel[0] participants: {s0.get('participants')}")
                    break

    # Sample top-level selections
    out("\n=== Sample top-level selections (first 5, then first non-milestone) ===")
    for i, s in enumerate(selections[:5] if isinstance(selections, list) else []):
        if isinstance(s, dict):
            out(f"  sel[{i}] label={s.get('label')} milestoneValue={s.get('milestoneValue')} participants={s.get('participants')}")

    out("\n  First selection WITHOUT milestoneValue AND label not ending in +:")
    for s in (selections if isinstance(selections, list) else []):
        if not isinstance(s, dict):
            continue
        if s.get("milestoneValue") is not None:
            continue
        label = str(s.get("label") or "")
        if label.endswith("+"):
            continue
        out(f"  FOUND: {json.dumps(s, ensure_ascii=False)[:500]}")
        break
    else:
        out("  None found — all selections are milestones.")

    # Also check if events structure contains subcategory info
    out("\n=== Events structure (first event) ===")
    if isinstance(events, list) and events:
        e0 = events[0]
        if isinstance(e0, dict):
            out(f"  event keys: {sorted(e0.keys())}")
            out(f"  eventId: {e0.get('eventId')}")
            out(f"  subcategories: {e0.get('subcategories') or e0.get('clientMetadata', {}).get('Subcategories')}")
    elif isinstance(events, dict):
        first_key = next(iter(events))
        e0 = events[first_key]
        out(f"  event keys: {sorted(e0.keys()) if isinstance(e0, dict) else type(e0).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
