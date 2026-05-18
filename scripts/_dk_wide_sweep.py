"""Wide brute-force sweep: 18000-100000 step 100, plus some specific guesses."""
import asyncio
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

MILESTONE_MT_IDS = {"12189","12190","12191","12192","12193","12194","12195","12196","12206",
                    "12197","12198","12199"}


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def count_classic_ou(payload: dict) -> int:
    buckets: dict[str, set[str]] = {}
    for market in (payload.get("markets") or []):
        if not isinstance(market, dict): continue
        mt = market.get("marketType") or {}
        if str(mt.get("id") or "") in MILESTONE_MT_IDS: continue
        if "milestone" in str(mt.get("name") or "").lower(): continue
        for sel_key in ("selections", "outcomes", "options"):
            for sel in (market.get(sel_key) or []):
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
                    if isinstance(p, dict):
                        participant = str(p.get("name") or "").strip()
                        if participant: break
                if not participant:
                    for k in ("playerName","participantName","runnerName"):
                        v = str(sel.get(k) or "").strip()
                        if v and v.lower() not in {"over","under"}:
                            participant = v; break
                line = None
                for lk in ("points","line","handicap","value"):
                    raw = sel.get(lk)
                    if raw is not None:
                        try: line = float(raw); break
                        except: pass
                if participant and line is not None:
                    ou_key = f"{participant}|{line}"
                    # Only count if participant looks like a person name (has space)
                    if " " in participant:
                        buckets.setdefault(ou_key, set()).add(side)
    # Also top-level selections
    for sel in (payload.get("selections") or []):
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
            if isinstance(p, dict):
                participant = str(p.get("name") or "").strip()
                if participant: break
        if not participant:
            for k in ("playerName","participantName","runnerName"):
                v = str(sel.get(k) or "").strip()
                if v and v.lower() not in {"over","under"}:
                    participant = v; break
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


async def probe(client: httpx.AsyncClient, sem: asyncio.Semaphore, sid: str) -> tuple[str, int, list[str]] | None:
    async with sem:
        eq = quote(f"$filter=leagueId eq '{NBA_LEAGUE_ID}' AND clientMetadata/Subcategories/any(s: s/Id eq '{sid}')", safe="")
        mq = quote(f"$filter=clientMetadata/subCategoryId eq '{sid}' AND tags/all(t: t ne 'SportcastBetBuilder')", safe="")
        url = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "league/leagueSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={eq}"
            f"&marketsQuery={mq}&include=Events&entity=events"
        )
        try:
            r = await client.get(url, timeout=10)
            if r.status_code != 200: return None
            data = r.json()
            markets = data.get("markets") or []
            if not (isinstance(markets, list) and markets): return None
            ou = count_classic_ou(data)
            names = mt_names(data)
            out(f"  {sid}: markets={len(markets)} ou_pairs={ou}{'  *** O/U ***' if ou > 0 else ''}  {names[:2]}")
            if ou > 0:
                import json, os
                os.makedirs("data/raw", exist_ok=True)
                with open(f"data/raw/dk_wide_ou_{sid}.json","w",encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            return (sid, ou, names)
        except Exception:
            return None


async def main() -> None:
    # Step 1: dense sweep of 18000-30000 step 50
    # Step 2: specific guesses from DK's known category structure
    ids: list[str] = []
    seen: set[str] = set()
    for i in range(18000, 30000, 50):
        s = str(i);
        if s not in seen: seen.add(s); ids.append(s)
    for i in range(30000, 100000, 500):
        s = str(i)
        if s not in seen: seen.add(s); ids.append(s)

    out(f"Wide sweep: {len(ids)} subcategory IDs (18000-100000 step varies)...")
    sem = asyncio.Semaphore(15)
    async with httpx.AsyncClient(headers=HEADERS) as client:
        results = await asyncio.gather(*[probe(client, sem, sid) for sid in ids])

    winners = [r for r in results if r and r[1] > 0]
    if winners:
        out(f'\n>>> FOUND! $env:DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS="{",".join(r[0] for r in winners)}"')
    else:
        out("\n>>> No classic player O/U found in 18000-100000 range.")
        out("    Please capture the DK network request from DevTools — that's the only reliable way.")


if __name__ == "__main__":
    asyncio.run(main())
