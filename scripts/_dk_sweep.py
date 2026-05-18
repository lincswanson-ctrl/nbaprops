"""Sweep DK subcategory IDs using the correct entity=events format."""
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

# fmt: off
CANDIDATE_IDS = [
    # Known from codebase
    "16477", "583", "584", "585", "586",
    "12195", "12196", "12197",
    "99901", "99902", "99903", "99904",
    # Sweep: around known milestone cluster 12180-12214
    *[str(i) for i in range(12180, 12215)],
    # Sweep: around 16477 in both directions
    *[str(i) for i in range(16440, 16500)],
    # Sweep: common DK prop sub-cat ranges seen in other sports
    *[str(i) for i in range(92000, 92030)],
    *[str(i) for i in range(100, 130)],
    # Additional larger ranges (step 1 is expensive but thorough)
    *[str(i) for i in range(16000, 16050)],
    *[str(i) for i in range(17000, 17050)],
    *[str(i) for i in range(18000, 18020)],
    *[str(i) for i in range(19000, 19020)],
    *[str(i) for i in range(20000, 20020)],
]
# fmt: on

# De-duplicate preserving order
_seen: set[str] = set()
CANDIDATE_IDS_DEDUP: list[str] = []
for _id in CANDIDATE_IDS:
    if _id not in _seen:
        _seen.add(_id)
        CANDIDATE_IDS_DEDUP.append(_id)


def _is_milestone(market: dict) -> bool:
    mt = market.get("marketType") or {}
    if not isinstance(mt, dict):
        return False
    name = str(mt.get("name") or "").lower()
    mid = str(mt.get("id") or "")
    if "milestone" in name:
        return True
    if mid in {"12189", "12190", "12191", "12192", "12193", "12194", "12206"}:
        return True
    return False


def _count_classic_ou(payload: dict) -> int:
    buckets: dict[str, set[str]] = {}
    for market in (payload.get("markets") or []):
        if not isinstance(market, dict) or _is_milestone(market):
            continue
        for sel_key in ("selections", "outcomes", "options"):
            for sel in (market.get(sel_key) or []):
                if not isinstance(sel, dict):
                    continue
                if sel.get("milestoneValue") is not None:
                    continue
                label = str(sel.get("label") or "").strip()
                if label.endswith("+"):
                    continue
                side = None
                for k in ("label", "name", "outcomeType"):
                    t = str(sel.get(k) or "").lower()
                    if "over" in t:
                        side = "over"
                        break
                    if "under" in t:
                        side = "under"
                        break
                if not side:
                    continue
                participant = ""
                for p in (sel.get("participants") or []):
                    if isinstance(p, dict):
                        participant = str(p.get("name") or "").strip()
                        if participant:
                            break
                if not participant:
                    for k in ("playerName", "participantName", "runnerName"):
                        v = str(sel.get(k) or "").strip()
                        if v and v.lower() not in {"over", "under"}:
                            participant = v
                            break
                line = None
                for lk in ("points", "line", "handicap", "value"):
                    raw = sel.get(lk)
                    if raw is not None:
                        try:
                            line = float(raw)
                            break
                        except (TypeError, ValueError):
                            pass
                if not participant or line is None:
                    continue
                buckets.setdefault(f"{participant}|{line}", set()).add(side)
    return sum(1 for sides in buckets.values() if len(sides) == 2)


def _market_type_names(payload: dict) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in (payload.get("markets") or []):
        if not isinstance(m, dict):
            continue
        mt = m.get("marketType") or {}
        if not isinstance(mt, dict):
            continue
        name = str(mt.get("name") or "").strip()
        mid = str(mt.get("id") or "").strip()
        label = f"{mid}:{name}" if mid else name
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


async def fetch(client: httpx.AsyncClient, sid: str) -> dict | None:
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
        r = await client.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


async def main() -> None:
    print(f"Sweeping {len(CANDIDATE_IDS_DEDUP)} subcategory IDs (entity=events format)...\n")
    sem = asyncio.Semaphore(8)
    interesting: list[tuple[str, int, int, list[str]]] = []

    async def probe(client: httpx.AsyncClient, sid: str) -> None:
        async with sem:
            payload = await fetch(client, sid)
            if payload is None:
                return
            events = payload.get("events") or []
            markets = payload.get("markets") or []
            n_events = len(events) if isinstance(events, list) else 0
            n_markets = len(markets) if isinstance(markets, list) else 0
            if n_events == 0 and n_markets == 0:
                return
            ou = _count_classic_ou(payload)
            types = _market_type_names(payload)
            interesting.append((sid, n_markets, ou, types))
            flag = "  *** CLASSIC O/U ***" if ou > 0 else ""
            line = f"  subcategory {sid:>7}: events={n_events} markets={n_markets} ou_pairs={ou}{flag}"
            # Encode safely for Windows console
            sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()

    async with httpx.AsyncClient(headers=HEADERS) as client:
        tasks = [probe(client, sid) for sid in CANDIDATE_IDS_DEDUP]
        await asyncio.gather(*tasks)

    interesting.sort(key=lambda x: (-x[2], -x[1]))

    header = "\n=== RESULTS sorted by classic O/U pairs ==="
    sys.stdout.buffer.write((header + "\n").encode("utf-8", errors="replace"))
    for sid, n_mkts, ou, types in interesting:
        flag = "  <-- CLASSIC O/U" if ou > 0 else ""
        row = f"  {sid}: markets={n_mkts} ou_pairs={ou}{flag}  types={types[:4]}"
        sys.stdout.buffer.write((row + "\n").encode("utf-8", errors="replace"))

    winners = [r for r in interesting if r[2] > 0]
    if winners:
        env_val = ",".join(r[0] for r in winners)
        msg = f'\n>>> FOUND! Set: $env:DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS="{env_val}"'
    else:
        msg = "\n>>> No classic O/U found. Props may not be posted yet or ID is outside swept range."

    sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    asyncio.run(main())
