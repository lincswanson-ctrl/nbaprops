"""Try alternate DK API paths that bypass the leagueSubcategory endpoint."""
import asyncio
import json
import sys
from urllib.parse import quote

import httpx

NBA_LEAGUE_ID = "42648"
EVENT_ID = "34109300"  # OKC @ Lakers
HEADERS_LEAGUE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://sportsbook.draftkings.com/leagues/basketball/nba",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
}
HEADERS_EVENT = {
    **HEADERS_LEAGUE,
    "Referer": f"https://sportsbook.draftkings.com/event/nba-okc-thunder-la-lakers/{EVENT_ID}",
}
MILESTONE_MT_IDS = {"12189","12190","12191","12192","12193","12194","12195","12196","12206","12197","12198","12199"}


def out(s: str) -> None:
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def analyze(payload) -> str:
    if not isinstance(payload, dict):
        return f"(non-dict: {type(payload).__name__} len={len(payload) if hasattr(payload,'__len__') else '?'})"
    markets = payload.get("markets") or []
    selections = payload.get("selections") or []
    events = payload.get("events") or []
    ou = 0
    buckets: dict[str, set[str]] = {}
    for sel in list(selections if isinstance(selections, list) else []) + [
        s for m in (markets if isinstance(markets, list) else []) if isinstance(m, dict)
        for key in ("selections","outcomes","options")
        for s in (m.get(key) or []) if isinstance(s, dict)
    ]:
        if not isinstance(sel, dict) or sel.get("milestoneValue") is not None: continue
        label = str(sel.get("label") or "")
        if label.endswith("+"): continue
        side = None
        for k in ("label","name","outcomeType"):
            t = str(sel.get(k) or "").lower()
            if "over" in t: side = "over"; break
            if "under" in t: side = "under"; break
        if not side: continue
        participant = ""
        for p in (sel.get("participants") or []):
            if isinstance(p, dict): participant = str(p.get("name") or "").strip()
            if participant: break
        if not participant:
            for k in ("playerName","participantName","runnerName"):
                v = str(sel.get(k) or "").strip()
                if v and v.lower() not in {"over","under"} and " " in v: participant = v; break
        line = None
        for lk in ("points","line","handicap","value"):
            raw = sel.get(lk)
            if raw is not None:
                try: line = float(raw); break
                except: pass
        if participant and line is not None and " " in participant:
            buckets.setdefault(f"{participant}|{line}", set()).add(side)
    ou = sum(1 for s in buckets.values() if len(s) == 2)
    types: dict[str, int] = {}
    for m in (markets if isinstance(markets, list) else []):
        if isinstance(m, dict):
            mt = m.get("marketType") or {}
            k = f"{mt.get('id')}:{mt.get('name')}"
            types[k] = types.get(k, 0) + 1
    top_types = sorted(types.items(), key=lambda x: -x[1])[:3]
    flag = "  *** PLAYER O/U ***" if ou > 0 else ""
    return (
        f"events={len(events) if isinstance(events,list) else '?'} "
        f"markets={len(markets) if isinstance(markets,list) else '?'} "
        f"player_ou={ou}{flag}  types={[t[0] for t in top_types]}"
    )


async def try_url(client: httpx.AsyncClient, label: str, url: str) -> dict | None:
    try:
        r = await client.get(url, timeout=20)
        if r.status_code == 200:
            try:
                data = r.json()
                out(f"  [{label}] HTTP 200 | {analyze(data)}")
                if isinstance(data, dict):
                    markets = data.get("markets") or []
                    # Check for player O/U
                    if "PLAYER O/U" in analyze(data):
                        path = f"data/raw/dk_alt_{label.replace('/', '_')[:40]}.json"
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        out(f"  Saved to {path}")
                return data
            except Exception:
                out(f"  [{label}] HTTP 200 but not JSON: {r.text[:100]}")
                return None
        else:
            out(f"  [{label}] HTTP {r.status_code}")
            return None
    except Exception as e:
        out(f"  [{label}] ERROR: {str(e)[:60]}")
        return None


async def main() -> None:
    # Try with both league and event referer headers
    async with httpx.AsyncClient(headers=HEADERS_EVENT) as client:
        out("=== DK v5 API via sportsbook-nash (event referer) ===")
        for path in [
            f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}?format=json",
            f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}/categories/1000?format=json",
            f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups/{NBA_LEAGUE_ID}/categories/1001?format=json",
            f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/events/{EVENT_ID}?format=json",
            f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/events/{EVENT_ID}/categories?format=json",
        ]:
            label = path.split("/api/")[1][:60]
            await try_url(client, label, path)

        out("\n=== Alternative DK API paths with event-specific URL ===")
        for base_url in [
            # Try without 'sites/US-SB' prefix
            f"https://sportsbook-nash.draftkings.com/api/sportscontent/controldata/league/leagueSubcategory/v1/markets",
            # Try with different site codes
            f"https://sportsbook-nash.draftkings.com/sites/US-NJ-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets",
            f"https://sportsbook-nash.draftkings.com/sites/US-VA-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets",
        ]:
            for sid in ["16477"]:
                eq = quote(f"$filter=leagueId eq '{NBA_LEAGUE_ID}' AND clientMetadata/Subcategories/any(s: s/Id eq '{sid}')", safe="")
                mq = quote(f"$filter=clientMetadata/subCategoryId eq '{sid}' AND tags/all(t: t ne 'SportcastBetBuilder')", safe="")
                url = f"{base_url}?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={eq}&marketsQuery={mq}&include=Events&entity=events"
                label = base_url.split("//")[1][:50]
                await try_url(client, label, url)

        out("\n=== Try DK 'categories' endpoint (v1) ===")
        # The DK website URL uses category/subcategory. Try that API path.
        for cat_id in ["1000", "1001", "1002", "687", "246", "583", "1028", "1159"]:
            url = (
                f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
                f"league/categories/v1/markets"
                f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}"
                f"&eventsQuery={quote(f'$filter=leagueId eq \"{NBA_LEAGUE_ID}\"', safe='')}"
                f"&marketsQuery={quote(f'$filter=clientMetadata/categoryId eq \"{cat_id}\"', safe='')}"
                f"&include=Events&entity=events"
            )
            await try_url(client, f"categories/v1/cat={cat_id}", url)

        out("\n=== Try DK event-specific markets with different query shapes ===")
        for ev_id in [EVENT_ID]:
            # Query for all markets for a specific event
            for mq_filter in [
                f"$filter=eventId eq '{ev_id}'",
                f"$filter=eventId eq '{ev_id}' AND tags/any(t: t eq 'PlayerProps')",
            ]:
                eq = quote(f"$filter=eventId eq '{ev_id}'", safe="")
                mq = quote(mq_filter, safe="")
                url = (
                    "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
                    "league/leagueSubcategory/v1/markets"
                    f"?isBatchable=false&templateVars={NBA_LEAGUE_ID}&eventsQuery={eq}"
                    f"&marketsQuery={mq}&include=Events,Markets,Selections&entity=events"
                )
                await try_url(client, f"event-filter/{mq_filter[:40]}", url)

        out("\n=== Try DK with templateVars=eventId instead of leagueId ===")
        for v in ["v1", "v2"]:
            for sid in ["16477", "16486", "16490", "16495", "16500"]:
                eq = quote(f"$filter=eventId eq '{EVENT_ID}'", safe="")
                mq = quote(f"$filter=clientMetadata/subCategoryId eq '{sid}'", safe="")
                url = (
                    f"https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/"
                    f"league/leagueSubcategory/{v}/markets"
                    f"?isBatchable=false&templateVars={EVENT_ID}&eventsQuery={eq}"
                    f"&marketsQuery={mq}&include=Events,Markets,Selections&entity=events"
                )
                await try_url(client, f"{v}/event-templateVar/sid={sid}", url)


if __name__ == "__main__":
    asyncio.run(main())
