"""Inspect 16477 payload structure and probe candidate subcategory IDs."""
import asyncio
import json
import re
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

KNOWN_IDS = [
    "16477",  # Points Milestones
    "583", "584", "585", "586",
    "12195", "12196", "12197",
    "99901", "99902", "99903", "99904",
]

EXTRA_SWEEP = list(range(12180, 12215)) + list(range(16478, 16510)) + list(range(16440, 16477))


async def fetch(client: httpx.AsyncClient, sid: str) -> dict | None:
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
    try:
        r = await client.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def market_type_summary(payload: dict) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in (payload.get("markets") or []):
        if not isinstance(m, dict):
            continue
        mt = m.get("marketType") or {}
        if not isinstance(mt, dict):
            continue
        name = str(mt.get("name") or "").strip()
        mid = str(mt.get("id") or "").strip()
        label = f"{mid}:{name}" if mid else name
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


def count_classic_ou(payload: dict) -> int:
    buckets: dict[str, set[str]] = {}
    for market in (payload.get("markets") or []):
        if not isinstance(market, dict):
            continue
        for key in ("selections", "outcomes", "options"):
            for sel in (market.get(key) or []):
                if not isinstance(sel, dict):
                    continue
                if sel.get("milestoneValue") is not None:
                    continue
                label = str(sel.get("label") or "").strip()
                if label.endswith("+"):
                    continue
                side = None
                for k in ("label", "name", "outcomeType"):
                    t = str(sel.get(k) or "").lower()
                    if "over" in t:
                        side = "over"
                        break
                    if "under" in t:
                        side = "under"
                        break
                if not side:
                    continue
                participant = ""
                for p in (sel.get("participants") or []):
                    if isinstance(p, dict):
                        participant = str(p.get("name") or "").strip()
                        if participant:
                            break
                if not participant:
                    for k in ("playerName", "participantName", "runnerName"):
                        v = str(sel.get(k) or "").strip()
                        if v and v.lower() not in {"over", "under"}:
                            participant = v
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


async def main() -> None:
    all_ids = KNOWN_IDS + [str(i) for i in EXTRA_SWEEP if str(i) not in set(KNOWN_IDS)]
    sem = asyncio.Semaphore(6)

    results: list[tuple[str, int, int, list[str]]] = []

    async def probe(client: httpx.AsyncClient, sid: str) -> None:
        async with sem:
            payload = await fetch(client, sid)
            if payload is None:
                return
            events = payload.get("events") or []
            markets = payload.get("markets") or []
            n_events = len(events) if isinstance(events, list) else 0
            n_markets = len(markets) if isinstance(markets, list) else 0
            if n_events == 0 and n_markets == 0:
                return
            ou = count_classic_ou(payload)
            types = market_type_summary(payload)
            results.append((sid, n_markets, ou, types))
            marker = "*** CLASSIC O/U ***" if ou > 0 else ""
            print(f"  {sid:>7}: events={n_events} markets={n_markets} ou_pairs={ou} {marker}")
            for t in types[:6]:
                print(f"           marketType: {t}")

    async with httpx.AsyncClient(headers=HEADERS) as client:
        # First dump the 16477 shape in detail
        print("=== Detailed inspection of subcategory 16477 ===")
        payload16 = await fetch(client, "16477")
        if payload16:
            markets = payload16.get("markets") or []
            print(f"  markets count: {len(markets)}")
            print(f"  market types: {market_type_summary(payload16)}")
            if markets and isinstance(markets[0], dict):
                print(f"  first market keys: {sorted(markets[0].keys())}")
            selections = payload16.get("selections") or []
            print(f"  top-level selections: {len(selections)}")
            if isinstance(selections, list) and selections and isinstance(selections[0], dict):
                s0 = selections[0]
                print(f"  first selection keys: {sorted(s0.keys())}")
                print(f"  first selection label: {s0.get('label')}")
                print(f"  first selection milestoneValue: {s0.get('milestoneValue')}")
                print(f"  first selection participants: {s0.get('participants')}")
                print(f"  first selection displayOdds: {s0.get('displayOdds')}")
            # Dump first market
            if markets and isinstance(markets[0], dict):
                m0 = markets[0]
                print(f"\n  first market name: {m0.get('name')}")
                print(f"  first market marketType: {m0.get('marketType')}")
                inner_sels = m0.get("selections") or m0.get("outcomes") or m0.get("options") or []
                print(f"  inner selections count: {len(inner_sels) if isinstance(inner_sels, list) else 0}")
                if isinstance(inner_sels, list) and inner_sels and isinstance(inner_sels[0], dict):
                    s = inner_sels[0]
                    print(f"  inner sel[0] keys: {sorted(s.keys())[:20]}")
                    print(f"  inner sel[0] label: {s.get('label')}")
                    print(f"  inner sel[0] milestoneValue: {s.get('milestoneValue')}")
                    print(f"  inner sel[0] participants: {s.get('participants')}")

        print("\n=== Probing other candidate subcategory IDs ===")
        tasks = [probe(client, sid) for sid in all_ids if sid != "16477"]
        await asyncio.gather(*tasks)

    print("\n=== Summary of IDs with markets ===")
    results.sort(key=lambda x: (-x[2], -x[1]))
    for sid, n_mkts, ou, types in results:
        marker = " <-- CLASSIC O/U" if ou > 0 else ""
        print(f"  {sid}: markets={n_mkts} ou_pairs={ou}{marker}  types={types[:3]}")

    winners = [r for r in results if r[2] > 0]
    if winners:
        print(f"\n>>> Set: $env:DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS=\"{','.join(r[0] for r in winners)}\"")
    else:
        print("\n>>> No classic O/U subcategory found in this sweep. Props may not be posted yet.")


if __name__ == "__main__":
    asyncio.run(main())
