"""
Try multiple DK API paths to find where classic O/U player props live.
Uses real event IDs from the active NBA slate.
"""
import asyncio
import json
import sys
from urllib.parse import quote

import httpx

NBA_LEAGUE_ID = "42648"
# Confirmed live event IDs (2026-05-09 dump from subcategory 16477)
EVENT_IDS = ["34109300", "34121947", "34121970"]

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


def has_classic_ou(payload: dict) -> int:
    """Count non-milestone Over/Under pairs."""
    buckets: dict[str, set[str]] = {}
    for market in (payload.get("markets") or []):
        if not isinstance(market, dict):
            continue
        mt = market.get("marketType") or {}
        mid = str(mt.get("id") or "")
        name = str(mt.get("name") or "").lower()
        if "milestone" in name or mid in {"12189","12190","12191","12192","12193","12194","12195","12196","12206"}:
            continue
        for sel_key in ("selections", "outcomes", "options"):
            for sel in (market.get(sel_key) or []):
                if not isinstance(sel, dict) or sel.get("milestoneValue") is not None:
                    continue
                label = str(sel.get("label") or "")
                if label.endswith("+"):
                    continue
                side = None
                for k in ("label", "name", "outcomeType"):
                    t = str(sel.get(k) or "").lower()
                    if "over" in t: side = "over"; break
                    if "under" in t: side = "under"; break
                if not side:
                    continue
                participant = ""
                for p in (sel.get("participants") or []):
                    if isinstance(p, dict):
                        participant = str(p.get("name") or "").strip()
                        if participant: break
                if not participant:
                    for k in ("playerName", "participantName", "runnerName"):
                        v = str(sel.get(k) or "").strip()
                        if v and v.lower() not in {"over", "under"}:
                            participant = v; break
                line = None
                for lk in ("points", "line", "handicap", "value"):
                    raw = sel.get(lk)
                    if raw is not None:
                        try: line = float(raw); break
                        except: pass
                if not participant or line is None:
                    continue
                buckets.setdefault(f"{participant}|{line}", set()).add(side)
    return sum(1 for s in buckets.values() if len(s) == 2)


def market_type_names(payload: dict) -> list[str]:
    seen: set[str] = set(); out_list: list[str] = []
    for m in (payload.get("markets") or []):
        if not isinstance(m, dict): continue
        mt = m.get("marketType") or {}
        label = f"{mt.get('id')}:{mt.get('name')}"
        if label not in seen:
            seen.add(label); out_list.append(label)
    return out_list


async def try_url(client: httpx.AsyncClient, label: str, url: str, *, method: str = "GET", body: dict | None = None) -> dict | None:
    try:
        if method == "POST":
            r = await client.post(url, json=body, timeout=15)
        else:
            r = await client.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                markets = data.get("markets") or []
                selections = data.get("selections") or []
                events = data.get("events") or []
                ou = has_classic_ou(data)
                types = market_type_names(data)
                flag = "  *** CLASSIC O/U ***" if ou > 0 else ""
                out(f"  [{label}] HTTP 200 | events={len(events) if isinstance(events,list) else '?'} markets={len(markets) if isinstance(markets,list) else '?'} ou_pairs={ou}{flag}")
                if types:
                    out(f"    types: {types[:5]}")
                if ou > 0:
                    return data
            elif isinstance(data, list):
                out(f"  [{label}] HTTP 200 (list len={len(data)})")
            return data if r.status_code == 200 else None
        else:
            out(f"  [{label}] HTTP {r.status_code}")
            return None
    except Exception as e:
        out(f"  [{label}] ERROR: {str(e)[:80]}")
        return None


async def main() -> None:
    eid = EVENT_IDS[0]  # OKC @ Lakers

    async with httpx.AsyncClient(headers=HEADERS) as client:

        out("=== 1. Event-specific endpoints (event-based API paths) ===")

        # Try the dkuinb path for event subcategories
        paths_to_try = [
            ("dkuinb/event/subcats",
             f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/dkuinb/v1/events/{eid}/subcategories"),
            ("dkuinb/event/markets",
             f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/dkuinb/v1/events/{eid}/markets"),
            ("dkuinb/event/mkts+sels",
             f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/dkuinb/v1/events/{eid}/markets?include=Markets,Selections"),
            ("controldata/event/v1/mkts",
             f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/event/v1/markets?templateVars={eid}&include=Events,Markets,Selections&entity=events"),
            ("controldata/event/props",
             f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/event/playerProps/v1/markets?templateVars={eid}&include=Events,Markets,Selections&entity=events"),
        ]
        # Also try league/event combo with broader filter (no subcategoryId restriction)
        broad_mq = quote("$filter=tags/all(t: t ne 'SportcastBetBuilder')", safe="")
        broad_eq = quote(f"$filter=eventId eq '{eid}'", safe="")
        paths_to_try += [
            ("league/event-filter/mkts",
             f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets"
             f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={broad_eq}&marketsQuery={broad_mq}&include=Events,Markets,Selections&entity=events"),
        ]

        for label, url in paths_to_try:
            result = await try_url(client, label, url)
            if result and isinstance(result, dict) and has_classic_ou(result) > 0:
                out(f"\n  *** FOUND CLASSIC O/U at {label} ***")
                with open("data/raw/dk_classic_ou_found.json", "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                return

        out("\n=== 2. Broader subcategory sweep (gaps we missed) ===")
        # Sweep ranges not covered before (step 1 throughout)
        sweep_ranges = [
            range(16050, 16440),   # gap between 16050 and 16440
            range(16500, 16700),   # above 16499
            range(16700, 17000, 5),
            range(15000, 16000, 5),
            range(17050, 18000, 5),
            range(20000, 20500, 5),
            range(25000, 25200, 5),
        ]
        all_ids: list[str] = []
        seen: set[str] = set()
        known_non_zero = {"16477","16478","16479","16480","16481","16482","16483","16484","16485",
                          "12188","12213","16463","16464","16471","16472","16473","16474","16475"}
        for r in sweep_ranges:
            for i in r:
                s = str(i)
                if s not in seen and s not in known_non_zero:
                    seen.add(s); all_ids.append(s)

        out(f"  Sweeping {len(all_ids)} additional subcategory IDs...")
        sem = asyncio.Semaphore(10)
        found: list[tuple[str, int, list[str]]] = []

        async def probe(sid: str) -> None:
            async with sem:
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
                    r = await client.get(url, timeout=12)
                    if r.status_code != 200:
                        return
                    data = r.json()
                    markets = data.get("markets") or []
                    if not (isinstance(markets, list) and markets):
                        return
                    ou = has_classic_ou(data)
                    types = market_type_names(data)
                    found.append((sid, ou, types))
                    flag = "  *** CLASSIC O/U ***" if ou > 0 else ""
                    out(f"  subcategory {sid}: markets={len(markets)} ou_pairs={ou}{flag}  types={types[:3]}")
                    if ou > 0:
                        with open(f"data/raw/dk_classic_ou_{sid}.json", "w", encoding="utf-8") as ff:
                            json.dump(data, ff, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        await asyncio.gather(*[probe(sid) for sid in all_ids])

        winners = [f for f in found if f[1] > 0]
        if winners:
            ids_str = ",".join(f[0] for f in winners)
            out(f'\n>>> FOUND! $env:DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS="{ids_str}"')
        else:
            out("\n>>> No classic O/U found in extended sweep either.")
            out("    The props may use an API path we haven't tried, or aren't posted yet.")


if __name__ == "__main__":
    asyncio.run(main())
