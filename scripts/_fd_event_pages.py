"""
Try FanDuel event-specific SBAPI pages to find player O/U markets.
FD event IDs from nav payload: 35573208, 35577676, 35577677 (May 10 games).
"""
import asyncio
import json
import re
import sys

import httpx

SBAPI_HOST = "sbapi.nj.sportsbook.fanduel.com"
SMP_HOST = "smp.nj.sportsbook.fanduel.com"
API_KEY = "FhMFpcPWXMeyZxOx"
FD_EVENT_IDS = ["35573208", "35577676", "35577677"]

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://sportsbook.fanduel.com",
    "Referer": "https://sportsbook.fanduel.com/navigation/nba",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}

_LADDER = re.compile(r"To Score \d+\+|To Record \d+\+|\d+\+ Made Threes", re.I)


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def analyze_fd_payload(payload: dict) -> dict:
    attachments = payload.get("attachments") or {}
    markets_map = attachments.get("markets") or {}
    events_map = attachments.get("events") or {}
    ou_markets = []
    ladder_markets = []
    other_markets = []
    for m in markets_map.values():
        if not isinstance(m, dict): continue
        name = str(m.get("marketName") or "")
        lower = name.lower()
        if _LADDER.search(name):
            ladder_markets.append(name)
        elif "player points" in lower or "player rebounds" in lower or "player assists" in lower:
            ou_markets.append(name)
        else:
            other_markets.append(name)
    return {
        "events": len(events_map),
        "total_markets": len(markets_map),
        "ou_candidates": ou_markets,
        "ladder_count": len(ladder_markets),
    }


async def try_fd_page(client: httpx.AsyncClient, label: str, url: str) -> dict | None:
    try:
        r = await client.get(url, timeout=20)
        if r.status_code == 200:
            data = r.json()
            s = analyze_fd_payload(data)
            flag = "  *** O/U FOUND ***" if s["ou_candidates"] else ""
            out(f"  [{label}] HTTP 200 | events={s['events']} markets={s['total_markets']} ou={len(s['ou_candidates'])} ladders={s['ladder_count']}{flag}")
            if s["ou_candidates"]:
                for name in s["ou_candidates"][:10]:
                    out(f"    O/U market: {name}")
                path = f"data/raw/fd_ou_{label.replace('/', '_').replace(' ', '_')}.json"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                out(f"  Saved to {path}")
            return data
        else:
            out(f"  [{label}] HTTP {r.status_code}")
            return None
    except Exception as e:
        out(f"  [{label}] ERROR: {str(e)[:80]}")
        return None


async def main() -> None:
    async with httpx.AsyncClient(headers=HEADERS) as client:
        out("=== FanDuel event-specific SBAPI page variations ===")
        for eid in FD_EVENT_IDS:
            for page_id in [
                f"nba-event-{eid}",
                f"event-{eid}",
                eid,
                f"nba-{eid}",
            ]:
                url = (
                    f"https://{SBAPI_HOST}/api/content-managed-page"
                    f"?page=CUSTOM&customPageId={page_id}&timezone=America%2FNew_York&_ak={API_KEY}"
                )
                result = await try_fd_page(client, f"CUSTOM/{page_id}", url)
                if result:
                    s = analyze_fd_payload(result)
                    if s["ou_candidates"]:
                        out("\n>>> Found O/U player props on FanDuel event page!")
                        return

            # Try EVENT page type
            for page_type in ["EVENT", "GAME", "MATCH"]:
                url = (
                    f"https://{SBAPI_HOST}/api/content-managed-page"
                    f"?page={page_type}&customPageId={eid}&timezone=America%2FNew_York&_ak={API_KEY}"
                )
                await try_fd_page(client, f"{page_type}/{eid}", url)

        out("\n=== FanDuel alternative API paths ===")
        for eid in FD_EVENT_IDS[:1]:
            paths = [
                f"https://{SBAPI_HOST}/api/inplay?eventIds={eid}&_ak={API_KEY}",
                f"https://{SBAPI_HOST}/api/event/{eid}?_ak={API_KEY}",
                f"https://{SBAPI_HOST}/api/events/{eid}/markets?_ak={API_KEY}",
                f"https://sbapi.nj.sportsbook.fanduel.com/api/player-props?eventId={eid}&_ak={API_KEY}",
            ]
            for url in paths:
                label = url.split("/api/")[1][:50]
                await try_fd_page(client, label, url)

        out("\n=== Trying FanDuel SMP for known market IDs (fetch all NBA player prop markets) ===")
        # Fetch lots of market IDs from the nav page and probe SMP for player props
        nav_url = (
            f"https://{SBAPI_HOST}/api/content-managed-page"
            f"?page=CUSTOM&customPageId=nba&timezone=America%2FNew_York&_ak={API_KEY}"
        )
        r_nav = await client.get(nav_url, timeout=20)
        if r_nav.status_code == 200:
            nav = r_nav.json()
            markets_map = nav.get("attachments", {}).get("markets", {})
            # Get ALL market IDs
            all_ids = []
            for m in markets_map.values():
                if not isinstance(m, dict): continue
                mid_raw = m.get("marketId")
                if mid_raw:
                    try:
                        mid = int(str(mid_raw).split(".")[0])
                        all_ids.append(mid)
                    except ValueError:
                        pass
            out(f"  Nav gave {len(all_ids)} market IDs. Fetching SMP prices for all...")
            smp_url = f"https://{SMP_HOST}/api/sports/fixedodds/readonly/v1/getMarketPrices?priceHistory=0"
            combined: list[dict] = []
            for start in range(0, len(all_ids), 40):
                chunk = all_ids[start:start+40]
                r = await client.post(smp_url, json={"marketIds": chunk})
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        combined.extend(data)
            out(f"  SMP returned {len(combined)} priced markets")
            ou_player_markets = []
            for m in combined:
                if not isinstance(m, dict): continue
                name = str(m.get("marketName") or "")
                lower = name.lower()
                if ("player" in lower or "points" in lower) and not _LADDER.search(name):
                    runners = m.get("runners") or []
                    has_over = any("over" in str(r.get("runnerName","")).lower() for r in runners if isinstance(r,dict))
                    has_under = any("under" in str(r.get("runnerName","")).lower() for r in runners if isinstance(r,dict))
                    if has_over and has_under:
                        ou_player_markets.append(m)
            out(f"  Classic player O/U markets from SMP: {len(ou_player_markets)}")
            for m in ou_player_markets[:5]:
                out(f"    {m.get('marketName')} id={m.get('marketId')}")
                runners = m.get("runners") or []
                for rn in runners[:2]:
                    out(f"      runner: {rn.get('runnerName')} handicap={rn.get('handicap')}")


if __name__ == "__main__":
    asyncio.run(main())
