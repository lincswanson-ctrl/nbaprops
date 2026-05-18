# Odds Endpoint Discovery (PR 03a)

Date: 2026-05-08
Method: Cursor browser MCP network capture on live public sportsbook pages.

## DraftKings

- **League page**: `https://sportsbook.draftkings.com/leagues/basketball/nba`
- **Player-props surface (typical)**: `?category=games&subcategory=player-props&nav_1=points`
- **JSON (XHR) — primary markets feed** (live-probed 2026-05-09):
  - `GET https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets`
  - **Query string (all URL-encoded):**
    - `isBatchable=false`
    - `templateVars=42648` (NBA template / league id)
    - `eventsQuery=$filter=leagueId eq '42648' AND clientMetadata/Subcategories/any(s: s/Id eq '<SUBCATEGORY_ID>')`
    - `marketsQuery=$filter=clientMetadata/subCategoryId eq '<SUBCATEGORY_ID>' AND tags/all(t: t ne 'SportcastBetBuilder')`
    - `include=Events&entity=events` (empty parse recovery may use `include=Events,Markets,Selections` with `entity=markets` or `entity=selections`)
  - **Headers that work with `httpx`:** `Accept: application/json`, `Referer: https://sportsbook.draftkings.com/leagues/basketball/nba`, modern Chrome `User-Agent`.
- **Confirmed working subcategory IDs** (probed 2026-05-09, `entity=events` format required; `entity=markets` returns HTTP 400):

  | Sub ID | marketType.id | marketType.name                    |
  |--------|---------------|------------------------------------|
  | 16477  | 12189         | Points Milestones                  |
  | 16478  | 12190         | Assists Milestones                 |
  | 16479  | 12191         | Rebounds Milestones                |
  | 16480  | 12206         | Three Pointers Made Milestones     |
  | 16481  | 12192         | Points + Assists Milestones        |
  | 16482  | 12193         | Points + Rebounds Milestones       |
  | 16483  | 12194         | Points + Rebounds + Assists Milestones |
  | 16484  | 12195         | Blocks Milestones                  |
  | 16485  | 12196         | Steals Milestones                  |

  All of these are milestone ladders — every row carries `milestoneValue` and `N+` labels. Parser and loader exclude them from `PropOffer` via `_dk_market_row_is_milestone()` and `_dk_selection_has_milestone_shape()`.

- **Subcategory IDs that return HTTP 400** (confirmed invalid as `leagueSubcategory` params): 12195, 12196, 12197 (these are `marketType.id` values, not subcategory IDs), 583–586, 99901–99904. `_SUBCATEGORY_IDS` was updated to remove these.

- **Classic O/U subcategory IDs: UNKNOWN** as of 2026-05-09. DK has not posted classic player-points O/U for 2026-05-10 playoff games yet. Classic O/U is typically posted day-of (4–12 h before tip-off). Run `scripts/_dk_sweep.py` on game day morning to discover them; set `DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS` when found.

- **Alternate classic totals subcategory:** discovered per jurisdiction/build via browser network capture on a single-game player props view; list comma-separated ids in **`DRAFTKINGS_PLAYER_POINTS_SUBCATEGORY_IDS`** (`Settings.draftkings_player_points_subcategory_ids`) to probe additional `leagueSubcategory` ids until paired Over/Under selections appear.
- **Event id mapping:** filter selections/markets with `eventId` matching `OddsEvent.bookmaker_event_id` (`events[*].eventId` in league payloads).

## FanDuel

- **Navigation page**: `https://sportsbook.fanduel.com/navigation/nba`
- **Bootstrap listing — SBAPI**  
  `GET https://<sbapi-state>/api/content-managed-page?page=CUSTOM&customPageId=nba&timezone=America%2FNew_York&_ak=<bootstrap_key>`  
  Host default **`sbapi.nj.sportsbook.fanduel.com`** (`Settings.fanduel_sbapi_host`). Response embeds `attachments.events` and `attachments.markets` (markets may lack fully hydrated runner odds).
- **Pricing overlay — SMP (`getMarketPrices`)**  
  - `POST https://<smp-state>/api/sports/fixedodds/readonly/v1/getMarketPrices?priceHistory=0`  
    Host default **`smp.nj.sportsbook.fanduel.com`** (`Settings.fanduel_smp_host`).  
  - **JSON body:** `{"marketIds":[734, ...]}` — integer ids parsed from navigation `marketId` tokens (strip fractional suffix if present). Response is a **JSON array** of market objects merged onto navigation rows before parsing.
  - **Headers:** `Content-Type: application/json`, `Origin: https://sportsbook.fanduel.com`, `Referer: https://sportsbook.fanduel.com/navigation/nba`, browser `User-Agent`.
- **Event ids:** navigation rows carry `eventId` (sometimes composite); normalize by stripping a fractional suffix before comparing to `OddsEvent.bookmaker_event_id`.
- **Ignored markets:** `To Score N+ Points`, `To Record N+ *`, `N+ Made Threes` ladders — excluded from canonical matching so they never hydrate `PropOffer`.
- **Live snapshot endpoint (metadata):** `GET https://api.sportsbook.fanduel.com/ips/inplayservice/v1.0/livedata?...&eventIds=<id>` (optional context).

## BetMGM

- **League page**:
  - `https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004`
  - Redirected to state host during capture: `https://www.nj.betmgm.com/...`
- **Observed API base**:
  - `https://www.nj.betmgm.com/cds-api/`
- **Observed key endpoints**:
  - Event counts:
    - `GET /cds-api/bettingoffer/counts-batch-v2?...`
  - Event offer pulls:
    - `GET /cds-api/bettingoffer/fixture-offers?...&fixtureIds=<id>&optionMarketIds=<csv>&...`
  - Event full view (good candidate for moneyline + props parser seed):
    - `GET /cds-api/bettingoffer/fixture-view?...&fixtureIds=<id>&offerMapping=All&...`
- **Critical request params observed**:
  - `x-bwin-accessid=<token>` query param used on `cds-api` requests.
  - `country`, `userCountry`, and `subdivision` region parameters.
  - `offerMapping` toggles filtered/full payloads.
- **Cloudflare / anti-bot note**:
  - Browser-captured requests succeeded; direct `httpx` may require Playwright fallback for some paths/regions.
- **Schema notes**:
  - `fixture-offers` maps fixture IDs + option market IDs to offer payloads.
  - `fixture-view` returns richer event market graph.
  - Exact key-path extraction requires response body capture in follow-up pass.

## Parser Schema Key Paths (implemented)

- DraftKings:
  - Events: `events[*].eventId`, `events[*].teamName1`, `events[*].teamName2`, `events[*].startDate`
  - Markets (milestone guard): `markets[*].marketType.id`, `markets[*].marketType.name`
  - Prices: `selections[*].label`, `selections[*].points`, `selections[*].displayOdds.american`, `selections[*].participants[0].name`
  - **Excluded from `PropOffer`:** markets whose type is Points/PRA/etc. **Milestones**, selections carrying `milestoneValue` / `N+` ladder labels.
- FanDuel:
  - Navigation events: `attachments.events[*].eventId`, `attachments.events[*].name`, `attachments.events[*].openDate`
  - Markets/runners: `markets[*].marketName`, `markets[*].runners[*].runnerName`, `runners[*].handicap`, `runners[*].result.type` (`OVER`/`UNDER`), `runners[*].winRunnerOdds.americanDisplayOdds.*`
  - **Pricing overlay:** merged rows from `getMarketPrices` responses share the same `marketId` keys as navigation.
- BetMGM:
  - Events: `fixtures[*].id`, `fixtures[*].competition.name.value`, `fixtures[*].participants[*].name.value`, `fixtures[*].startDate`
  - Props/Moneyline: `fixture.games[*].name.value`, `fixture.games[*].results[*].name.value`, `...line`, `...americanOdds`, `...playerId`

## Discovery Status vs Spec 03a

- Completed:
  - Live endpoint families identified for DK/FD/MGM.
  - NBA league id confirmation in DK traffic (`42648`).
  - Raw request signature fixtures added for each book (see `tests/fixtures/odds/...`).
  - Playwright caution documented for BetMGM.
- Pending before PR 03b parser implementation:
  - Full DK category/subcategory ID map for all 12 canonical markets.
  - Exact JSON key-path schema extraction from response bodies for all books.
