"""Inspect FanDuel SBAPI navigation payload for NBA markets."""
import asyncio
import re
import sys
from collections import Counter

import httpx

SBAPI_HOST = "sbapi.nj.sportsbook.fanduel.com"
SMP_HOST = "smp.nj.sportsbook.fanduel.com"
API_KEY = "FhMFpcPWXMeyZxOx"

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

_TO_SCORE = re.compile(r"To Score \d+\+ Points", re.I)
_MADE_THREES = re.compile(r"\d+\+ Made Threes", re.I)
_TO_RECORD = re.compile(r"To Record \d+\+", re.I)


def is_ladder(name: str) -> bool:
    return bool(_TO_SCORE.search(name) or _MADE_THREES.search(name) or _TO_RECORD.search(name))


def out(s: str) -> None:
    sys.stdout.buffer.write((s + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


async def main() -> None:
    url = (
        f"https://{SBAPI_HOST}/api/content-managed-page"
        f"?page=CUSTOM&customPageId=nba&timezone=America%2FNew_York&_ak={API_KEY}"
    )
    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        r = await client.get(url)
        out(f"FanDuel SBAPI navigation: HTTP {r.status_code}")
        if r.status_code != 200:
            out(f"  body: {r.text[:500]}")
            return

        payload = r.json()
        attachments = payload.get("attachments") or {}
        events_map = attachments.get("events") or {}
        markets_map = attachments.get("markets") or {}

        out(f"  events: {len(events_map)}")
        out(f"  markets: {len(markets_map)}")

        # Summarise events
        out("\n=== Events ===")
        for eid, ev in list(events_map.items())[:20]:
            name = ev.get("name") or ""
            start = ev.get("openDate") or ev.get("startDate") or ""
            out(f"  {eid}: {name}  start={start}")

        # Summarise market names
        out("\n=== Market name breakdown ===")
        name_counter: Counter = Counter()
        player_points_markets: list[dict] = []
        for m in markets_map.values():
            if not isinstance(m, dict):
                continue
            mname = str(m.get("marketName") or "").strip()
            name_counter[mname] += 1
            lower = mname.lower()
            if "player points" in lower and not is_ladder(mname):
                player_points_markets.append(m)

        for mname, cnt in name_counter.most_common(30):
            ladder_flag = "  [LADDER]" if is_ladder(mname) else ""
            out(f"  {cnt:>4}x  {mname}{ladder_flag}")

        out(f"\n=== Classic 'Player Points' O/U markets (non-ladder): {len(player_points_markets)} ===")
        if player_points_markets:
            # Get market IDs to fetch prices
            sample = player_points_markets[:5]
            for m in sample:
                out(f"  marketId={m.get('marketId')} name={m.get('marketName')} eventId={m.get('eventId')}")
                runners = m.get("runners") or []
                out(f"    runners: {len(runners)}")
                for runner in runners[:3]:
                    out(f"      {runner}")

            # Fetch prices for the first batch
            all_ids = []
            for m in player_points_markets:
                mid_raw = m.get("marketId")
                if mid_raw is not None:
                    try:
                        mid = int(str(mid_raw).split(".")[0])
                        all_ids.append(mid)
                    except ValueError:
                        pass

            if all_ids:
                out(f"\n=== Fetching SMP prices for {len(all_ids[:40])} markets ===")
                smp_url = (
                    f"https://{SMP_HOST}/api/sports/fixedodds/readonly/v1/getMarketPrices"
                    "?priceHistory=0"
                )
                r2 = await client.post(smp_url, json={"marketIds": all_ids[:40]})
                out(f"  SMP HTTP {r2.status_code}")
                if r2.status_code == 200:
                    price_data = r2.json()
                    if isinstance(price_data, list):
                        out(f"  returned {len(price_data)} priced markets")
                        for pm in price_data[:3]:
                            out(f"    keys: {list(pm.keys())[:15]}")
                            runners = pm.get("runners") or []
                            out(f"    runners: {len(runners)}")
                            for rn in runners[:2]:
                                out(f"      {rn}")
                    else:
                        out(f"  shape: {list(price_data.keys())[:10] if isinstance(price_data, dict) else type(price_data).__name__}")
                else:
                    out(f"  body: {r2.text[:400]}")
        else:
            out("  None found — FanDuel may not have posted Player Points O/U yet.")
            out("  Ladder / milestone market names seen:")
            for mname, cnt in name_counter.most_common():
                if is_ladder(mname) or "points" in mname.lower():
                    out(f"    {cnt}x {mname}")


if __name__ == "__main__":
    asyncio.run(main())
