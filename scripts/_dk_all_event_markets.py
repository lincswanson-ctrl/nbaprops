"""
Fetch ALL markets for a specific DK event without subcategory filtering.
Also try alternate URL patterns and a wider subcategory sweep.
"""
import asyncio
import json
import sys
from urllib.parse import quote

import httpx

NBA_LEAGUE_ID = "42648"
EVENT_ID = "34109300"  # OKC @ Lakers, 2026-05-10

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"https://sportsbook.draftkings.com/event/nba-okc-thunder-la-lakers/{EVENT_ID}",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def market_summary(payload: dict) -> dict:
    markets = payload.get("markets") or []
    selections = payload.get("selections") or []
    events = payload.get("events") or []
    types: dict[str, int] = {}
    for m in (markets if isinstance(markets, list) else []):
        if not isinstance(m, dict): continue
        mt = m.get("marketType") or {}
        k = f"{mt.get('id')}:{mt.get('name')}"
        types[k] = types.get(k, 0) + 1
    ou_buckets: dict[str, set[str]] = {}
    for sel_src in [selections] + [m.get("selections") or m.get("outcomes") or [] for m in (markets if isinstance(markets, list) else [])]:
        for s in (sel_src if isinstance(sel_src, list) else []):
            if not isinstance(s, dict) or s.get("milestoneValue") is not None: continue
            label = str(s.get("label") or "")
            if label.endswith("+"): continue
            side = None
            for k in ("label", "name", "outcomeType"):
                t = str(s.get(k) or "").lower()
                if "over" in t: side = "over"; break
                if "under" in t: side = "under"; break
            if not side: continue
            participant = ""
            for p in (s.get("participants") or []):
                if isinstance(p, dict):
                    participant = str(p.get("name") or "").strip()
                    if participant: break
            if not participant:
                for k in ("playerName", "participantName"):
                    v = str(s.get(k) or "").strip()
                    if v and v.lower() not in {"over","under"}: participant = v; break
            line = None
            for lk in ("points","line","handicap","value"):
                raw = s.get(lk)
                if raw is not None:
                    try: line = float(raw); break
                    except: pass
            if participant and line is not None:
                ou_buckets.setdefault(f"{participant}|{line}", set()).add(side)
    ou_pairs = sum(1 for s in ou_buckets.values() if len(s) == 2)
    return {
        "events": len(events) if isinstance(events, list) else 0,
        "markets": len(markets) if isinstance(markets, list) else 0,
        "selections": len(selections) if isinstance(selections, list) else 0,
        "ou_pairs": ou_pairs,
        "types": types,
    }


async def probe(client: httpx.AsyncClient, label: str, url: str) -> dict | None:
    try:
        r = await client.get(url, timeout=20)
        if r.status_code == 200:
            data = r.json()
            if not isinstance(data, dict):
                out(f"  [{label}] HTTP 200 (non-dict)")
                return data
            s = market_summary(data)
            flag = "  *** O/U ***" if s["ou_pairs"] > 0 else ""
            out(f"  [{label}] HTTP 200 | events={s['events']} markets={s['markets']} ou_pairs={s['ou_pairs']}{flag}")
            for k, v in list(s["types"].items())[:8]:
                out(f"    {v}x {k}")
            if s["ou_pairs"] > 0:
                path = f"data/raw/dk_ou_{label.replace('/', '_')}.json"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                out(f"  Saved to {path}")
                return data
            return data
        else:
            out(f"  [{label}] HTTP {r.status_code}")
            return None
    except Exception as e:
        out(f"  [{label}] ERROR: {str(e)[:80]}")
        return None


async def main() -> None:
    async with httpx.AsyncClient(headers=HEADERS) as client:

        out("=== Broad event-scoped queries (no subcategory filter) ===")

        # 1. All markets for the event using different filter approaches
        for tag_filter in [
            "tags/all(t: t ne 'SportcastBetBuilder')",
            "tags/any(t: t eq 'PlayerProps')",
            "tags/any(t: t eq 'PlayerPropOU')",
            "tags/any(t: t eq 'OverUnder')",
        ]:
            mq = quote(tag_filter, safe="")
            eq = quote(f"$filter=leagueId eq '{NBA_LEAGUE_ID}'", safe="")
            url = (
                "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
                "league/leagueSubcategory/v1/markets"
                f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={eq}"
                f"&marketsQuery={mq}&include=Events,Markets,Selections&entity=events"
            )
            await probe(client, f"tag:{tag_filter[:30]}", url)

        out("\n=== v3/v4 market endpoints ===")
        for v in ["v2", "v3", "v4"]:
            eq = quote(f"$filter=leagueId eq '{NBA_LEAGUE_ID}'", safe="")
            url = (
                f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
                f"league/leagueSubcategory/{v}/markets"
                f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={eq}"
                "&include=Events&entity=events"
            )
            await probe(client, f"leagueSubcat/{v}", url)

        out("\n=== Nash event-direct endpoints ===")
        for path in [
            f"event/{EVENT_ID}/markets",
            f"event/{EVENT_ID}/playerprops",
            f"events/{EVENT_ID}/markets",
            f"events/{EVENT_ID}/playerprops",
        ]:
            for base in ["controldata", "dkuinb"]:
                url = f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/{base}/v1/{path}"
                await probe(client, f"{base}/{path[:40]}", url)

        out("\n=== Sweep subcategory IDs in untouched ranges (step 1) ===")
        sem = asyncio.Semaphore(12)
        found: list[tuple[str, int, dict]] = []

        async def sweep(sid: str) -> None:
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
                    if r.status_code != 200: return
                    data = r.json()
                    s = market_summary(data)
                    if s["markets"] == 0: return
                    found.append((sid, s["ou_pairs"], s))
                    flag = "  *** O/U ***" if s["ou_pairs"] > 0 else ""
                    out(f"  {sid}: markets={s['markets']} ou_pairs={s['ou_pairs']}{flag}  {list(s['types'].keys())[:2]}")
                    if s["ou_pairs"] > 0:
                        with open(f"data/raw/dk_sweep_ou_{sid}.json","w",encoding="utf-8") as ff:
                            json.dump(data, ff, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        # Sweep remaining gaps
        prev_found = {
            "16305","16312","16313","16314","16315","16316","16324","16325",
            "16380","16381","16382","16383","16384","16385","16386","16387","16388","16389","16390","16391","16392",
            "16463","16464","16471","16472","16473","16474","16475",
            "16477","16478","16479","16480","16481","16482","16483","16484","16485",
            "16512","16551","16552","16553","16555","16559","16561","16563","16564","16565","16566",
            "16601","16602","16630","16656","16661","16662","16664","16698","16720","16825",
            "15610","17515","17695","17700","12188","12213",
        }
        sweep_ids = []
        sweep_seen: set[str] = set()
        for ranges in [
            range(15000, 15610),
            range(15611, 16000),
            range(16000, 16305),
            range(16317, 16380),
            range(16393, 16463),
            range(16466, 16471),
            range(16476, 16477),
            range(16486, 16512),
            range(16513, 16551),
            range(16554, 16555),
            range(16556, 16559),
            range(16560, 16561),
            range(16562, 16563),
            range(16567, 16601),
            range(16603, 16630),
            range(16631, 16656),
            range(16657, 16661),
            range(16663, 16664),
            range(16665, 16698),
            range(16699, 16720),
            range(16721, 16825),
            range(16826, 17000),
            range(17001, 17515),
            range(17516, 17695),
            range(17701, 18000),
        ]:
            for i in ranges:
                s = str(i)
                if s not in sweep_seen and s not in prev_found:
                    sweep_seen.add(s); sweep_ids.append(s)

        out(f"  {len(sweep_ids)} IDs to check in gaps...")
        await asyncio.gather(*[sweep(sid) for sid in sweep_ids])

        winners = [f for f in found if f[1] > 0]
        if winners:
            out(f'\n>>> FOUND! $env:DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS="{",".join(f[0] for f in winners)}"')
        else:
            out("\n>>> Still no classic O/U in full 15000-18000 range sweep.")
            out("    Try: what URL does the DK website request when you view Points player props?")
            out("    Open browser DevTools > Network > filter 'markets' > click a player prop O/U on DK.")


if __name__ == "__main__":
    asyncio.run(main())
