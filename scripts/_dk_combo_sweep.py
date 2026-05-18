"""Sweep 13000-14500 on event endpoint for steals/combo/PRA O/U markets."""
import asyncio
import sys
from urllib.parse import quote
import httpx

EVENT_ID = "34109300"
HEADERS = {
    "Accept": "application/json",
    "Referer": f"https://sportsbook.draftkings.com/event/nba-okc-thunder-la-lakers/{EVENT_ID}",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
}


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


async def probe(client: httpx.AsyncClient, sem: asyncio.Semaphore, sid: str) -> None:
    async with sem:
        tv = quote(f"{EVENT_ID},{sid}", safe="")
        mq = quote(
            f"$filter=eventId eq '{EVENT_ID}' AND clientMetadata/subCategoryId eq '{sid}' AND tags/all(t: t ne 'SportcastBetBuilder')",
            safe="",
        )
        url = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "event/eventSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={tv}&marketsQuery={mq}&entity=markets"
        )
        try:
            r = await client.get(url, timeout=10)
            if r.status_code != 200: return
            data = r.json()
            markets = data.get("markets") or []
            if not (isinstance(markets, list) and markets): return
            names: list[str] = []
            for m in markets:
                if isinstance(m, dict):
                    mt = m.get("marketType") or {}
                    k = f"{mt.get('id')}:{mt.get('name')}"
                    if k not in names: names.append(k)
            # Only print if it looks player-related
            name_str = str(names[:3])
            if any(kw in name_str.lower() for kw in ("o/u", "ou", "steals", "turnover", "combo", "assists", "rebound", "points", "blocks")):
                out(f"{sid}: markets={len(markets)} types={names[:3]}")
        except Exception:
            pass


async def main() -> None:
    sem = asyncio.Semaphore(15)
    async with httpx.AsyncClient(headers=HEADERS) as client:
        tasks = [probe(client, sem, str(i)) for i in range(13000, 14500)]
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
