"""
Use Playwright to navigate DK player props page and capture the XHR
request that returns classic Over/Under player prop markets.

Runs headless Chromium, navigates to the OKC @ Lakers game player props,
intercepts network traffic, and extracts the subcategory/category IDs.
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright, Request, Response


EVENT_ID = "34109300"   # OKC @ Lakers, 2026-05-10
NBA_LEAGUE_ID = "42648"


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


async def main() -> None:
    captured: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        async def on_response(response: Response) -> None:
            url = response.url
            if "draftkings.com" not in url:
                return
            # Capture any JSON response related to markets or player props
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            interesting = (
                "v5" in url
                or "markets" in url
                or "playerProps" in url
                or "subcategor" in url
                or "categor" in url
            )
            if not interesting:
                return
            try:
                body = await response.json()
                captured.append({"url": url, "status": response.status, "body": body})
                out(f"  CAPTURED: [{response.status}] {url[:120]}")
            except Exception:
                pass

        page.on("response", on_response)

        # Navigate to the DK NBA page first
        out("Navigating to DK NBA page...")
        await page.goto("https://sportsbook.draftkings.com/leagues/basketball/nba", timeout=30000)
        await page.wait_for_timeout(2000)

        # Navigate to the specific game's player props page
        event_url = (
            f"https://sportsbook.draftkings.com/event/nba-okc-thunder-la-lakers/{EVENT_ID}"
            "?category=game&subcategory=player-props&nav_1=points"
        )
        out(f"\nNavigating to game player props page...")
        await page.goto(event_url, timeout=30000)
        await page.wait_for_timeout(4000)

        # Also try clicking "Player Props" or "Points" tab if visible
        try:
            # Look for the Points tab
            points_tab = page.locator("text=Points").first
            if await points_tab.is_visible(timeout=2000):
                out("Clicking 'Points' tab...")
                await points_tab.click()
                await page.wait_for_timeout(2000)
        except Exception:
            pass

        # Also try the league-level player props page
        out("\nNavigating to NBA player props league page...")
        await page.goto(
            "https://sportsbook.draftkings.com/leagues/basketball/nba"
            "?category=player-props&subcategory=points",
            timeout=30000,
        )
        await page.wait_for_timeout(3000)

        await browser.close()

    out(f"\n=== Captured {len(captured)} JSON responses ===")

    Path("data/raw").mkdir(parents=True, exist_ok=True)

    v5_responses = []
    player_ou_responses = []

    for i, item in enumerate(captured):
        url = item["url"]
        body = item["body"]
        out(f"\n[{i}] {url[:100]}")

        if "v5" in url:
            v5_responses.append(item)
            out("  ^ v5 API response!")
            # Try to extract category/subcategory IDs
            if isinstance(body, dict):
                eg = body.get("eventGroup") or {}
                if isinstance(eg, dict):
                    cats = eg.get("offerCategories") or []
                    out(f"  offerCategories count: {len(cats)}")
                    for cat in cats[:10]:
                        if isinstance(cat, dict):
                            out(f"    cat id={cat.get('id')} name={cat.get('name')}")
                            subcats = cat.get("offerSubcategoryDescriptors") or []
                            for sc in subcats[:5]:
                                if isinstance(sc, dict):
                                    out(f"      subcat id={sc.get('id')} name={sc.get('name')}")

        # Check for player O/U
        if isinstance(body, dict):
            markets = body.get("markets") or []
            selections = body.get("selections") or []
            if isinstance(markets, list) and markets:
                mts = set()
                for m in markets:
                    if isinstance(m, dict):
                        mt = m.get("marketType") or {}
                        mts.add(f"{mt.get('id')}:{mt.get('name')}")
                out(f"  market types: {list(mts)[:5]}")
                # Check for O/U pairs
                ou_count = 0
                buckets: dict[str, set[str]] = {}
                for sel in list(selections if isinstance(selections, list) else []):
                    if not isinstance(sel, dict) or sel.get("milestoneValue") is not None: continue
                    label = str(sel.get("label") or "")
                    if label.endswith("+"): continue
                    side = None
                    for k in ("label","name"):
                        t = str(sel.get(k) or "").lower()
                        if "over" in t: side = "over"; break
                        if "under" in t: side = "under"; break
                    if not side: continue
                    participant = ""
                    for p in (sel.get("participants") or []):
                        if isinstance(p, dict): participant = str(p.get("name") or ""); break
                    line = None
                    for lk in ("points","line","handicap"):
                        raw = sel.get(lk)
                        if raw is not None:
                            try: line = float(raw); break
                            except: pass
                    if participant and line is not None and " " in participant:
                        buckets.setdefault(f"{participant}|{line}", set()).add(side)
                ou_count = sum(1 for s in buckets.values() if len(s) == 2)
                if ou_count > 0:
                    out(f"  *** PLAYER O/U: {ou_count} pairs ***")
                    player_ou_responses.append(item)

    # Save all v5 responses
    for i, item in enumerate(v5_responses):
        path = f"data/raw/dk_playwright_v5_{i}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=2)
        out(f"Saved v5 response to {path}")

    if player_ou_responses:
        path = "data/raw/dk_playwright_ou.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(player_ou_responses[0], f, ensure_ascii=False, indent=2)
        out(f"\n*** Saved player O/U response to {path} ***")
        out("Check that file for the subcategory ID / API URL structure.")
    else:
        out("\nNo classic player O/U responses captured.")
        out("All captured URLs saved below for manual inspection:")
        for item in captured:
            out(f"  {item['url'][:120]}")

        # Save all captures for inspection
        path = "data/raw/dk_playwright_all_captures.json"
        saves = [{"url": c["url"], "status": c["status"]} for c in captured]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(saves, f, ensure_ascii=False, indent=2)
        out(f"URL list saved to {path}")


if __name__ == "__main__":
    asyncio.run(main())
