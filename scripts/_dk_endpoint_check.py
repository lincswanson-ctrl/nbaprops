"""Quick reachability check for DK nash endpoint."""
import asyncio
import httpx
from urllib.parse import quote

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


async def main() -> None:
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        # 1. No-filter (just leagueId), entity=events — the original working format
        events_query = quote(f"$filter=leagueId eq '{NBA_LEAGUE_ID}'", safe="")
        url = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "league/leagueSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={events_query}"
            "&include=Events&entity=events"
        )
        r = await client.get(url)
        print(f"No-filter entity=events: HTTP {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"  top-level keys: {list(data.keys())[:15]}")
            events = data.get("events") or []
            markets = data.get("markets") or []
            print(f"  events={len(events) if isinstance(events, list) else type(events).__name__}")
            print(f"  markets={len(markets) if isinstance(markets, list) else type(markets).__name__}")
        else:
            print(f"  response body: {r.text[:400]}")

        # 2. subcategory 16477, entity=events (original scraper format)
        sid = "16477"
        eq = quote(
            f"$filter=leagueId eq '{NBA_LEAGUE_ID}' AND clientMetadata/Subcategories/any(s: s/Id eq '{sid}')",
            safe="",
        )
        mq = quote(
            f"$filter=clientMetadata/subCategoryId eq '{sid}' AND tags/all(t: t ne 'SportcastBetBuilder')",
            safe="",
        )
        url2 = (
            "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
            "league/leagueSubcategory/v1/markets"
            f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={eq}"
            f"&marketsQuery={mq}&include=Events&entity=events"
        )
        r2 = await client.get(url2)
        print(f"\nSubcategory 16477 entity=events: HTTP {r2.status_code}")
        if r2.status_code == 200:
            data2 = r2.json()
            print(f"  top-level keys: {list(data2.keys())[:15]}")
            events2 = data2.get("events") or []
            markets2 = data2.get("markets") or []
            print(f"  events={len(events2) if isinstance(events2, list) else type(events2).__name__}")
            print(f"  markets={len(markets2) if isinstance(markets2, list) else type(markets2).__name__}")
        else:
            print(f"  response body: {r2.text[:400]}")

        # 3. Eventgroup endpoint (broad fallback)
        url3 = f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}?format=json"
        r3 = await client.get(url3)
        print(f"\nEventgroup endpoint: HTTP {r3.status_code}")
        if r3.status_code == 200:
            data3 = r3.json()
            print(f"  top-level keys: {list(data3.keys())[:15]}")
        else:
            print(f"  response body: {r3.text[:400]}")


if __name__ == "__main__":
    asyncio.run(main())
