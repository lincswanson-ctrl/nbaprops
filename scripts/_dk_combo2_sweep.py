"""Find combo O/U and turnovers by sweeping wider ranges."""
import asyncio, sys
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
            out(f"{sid}: markets={len(markets)} {names[:4]}")
        except Exception:
            pass


async def main() -> None:
    # Search around 12497 (3PM) for combos; also 13500-14000 gap; also 12600-13000
    ids: list[str] = []
    seen: set[str] = set()
    for i in list(range(12498, 12560)) + list(range(12560, 12700, 2)) + list(range(13782, 14000)):
        s = str(i)
        if s not in seen: seen.add(s); ids.append(s)
    # Also specific guesses
    for s in ["12500","12501","12502","12503","12504","12505","12510","12515","12520","12530","12540","12550"]:
        if s not in seen: seen.add(s); ids.append(s)

    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient(headers=HEADERS) as client:
        tasks = [probe(client, sem, sid) for sid in ids]
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
