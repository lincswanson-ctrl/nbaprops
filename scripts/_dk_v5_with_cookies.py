"""
Try DK v5 API by first fetching the homepage to get session cookies,
then using those cookies on the v5 eventgroups/categories endpoint.
"""
import asyncio
import json
import sys

import httpx

NBA_LEAGUE_ID = "42648"
EVENT_ID = "34109300"  # OKC @ Lakers

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
}

API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
}


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def show_payload(data: dict, label: str) -> None:
    markets = data.get("markets") or []
    categories = data.get("eventGroup", {}).get("offerCategories") if isinstance(data.get("eventGroup"), dict) else []
    out(f"  [{label}] keys: {list(data.keys())[:10]}")
    if categories:
        out(f"  categories: {len(categories)}")
        for cat in categories[:5]:
            if isinstance(cat, dict):
                out(f"    cat id={cat.get('id')} name={cat.get('name')}")
                subcats = cat.get("offerSubcategoryDescriptors") or []
                for sc in subcats[:5]:
                    if isinstance(sc, dict):
                        out(f"      subcat id={sc.get('id')} name={sc.get('name')}")
    out(f"  eventGroup: {type(data.get('eventGroup')).__name__}")
    out(f"  markets: {len(markets) if isinstance(markets, list) else type(markets).__name__}")


async def main() -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        # Step 1: hit the homepage to get cookies/session
        out("Step 1: Fetching DK homepage for cookies...")
        r0 = await client.get("https://sportsbook.draftkings.com/", headers=HEADERS)
        out(f"  Homepage: HTTP {r0.status_code}, cookies: {list(client.cookies.keys())}")

        # Step 2: hit the league page
        out("Step 2: Fetching league page...")
        r1 = await client.get(
            f"https://sportsbook.draftkings.com/leagues/basketball/nba",
            headers={**HEADERS, "Sec-Fetch-Site": "same-origin", "Referer": "https://sportsbook.draftkings.com/"},
        )
        out(f"  League page: HTTP {r1.status_code}, cookies now: {list(client.cookies.keys())}")

        # Step 3: try the v5 eventgroups API
        out("Step 3: Trying v5 eventgroups API...")
        for url in [
            f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}?format=json",
            f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}/categories/1000?format=json",
            f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}/categories/1001?format=json",
            f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}/categories/1002?format=json",
        ]:
            r = await client.get(url, headers={
                **API_HEADERS,
                "Referer": "https://sportsbook.draftkings.com/leagues/basketball/nba",
            })
            out(f"  {url.split('api/')[1][:60]}: HTTP {r.status_code}")
            if r.status_code == 200:
                try:
                    data = r.json()
                    if isinstance(data, dict):
                        show_payload(data, url.split("api/")[1][:40])
                        with open("data/raw/dk_v5_found.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        out("  Saved to data/raw/dk_v5_found.json")
                except Exception as e:
                    out(f"  JSON parse error: {e}")

        # Step 4: Try the nash domain with cookies
        out("\nStep 4: Trying sportsbook-nash v5 with cookies...")
        for url in [
            f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}?format=json",
        ]:
            r = await client.get(url, headers={
                **API_HEADERS,
                "Referer": "https://sportsbook.draftkings.com/leagues/basketball/nba",
            })
            out(f"  nash v5: HTTP {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                out(f"  keys: {list(data.keys())[:10]}")


if __name__ == "__main__":
    asyncio.run(main())
