# Spec: Two-Sided Edges, Under Prices and Vig-Free Market Probability

**Module:** `betting/market.py` (pure functions) + `orchestration/pregame.py` odds/edge steps
**Migration:** `data_pipeline/schemas/006_under_prices.sql`
**Owner:** Claude (Lincoln approves)
**Depends on:** spec 03 (`prop_lines` already stores `over_price` and `under_price`), spec 05
**Status:** Draft — implemented with the defaults below; open decisions at the bottom

---

## Problems (found in the 2026-11-10 smoke tests)

1. **Unders can never be picked.** `run_edge_selection` sets `side = "under"` for negative
   edges, then rejects them because the signed edge is compared to a positive threshold.
2. **No under prices downstream.** `_copy_raw_odds` copies only `prop_lines.over_price` into
   `odds.price`; scrapers already collect under prices.
3. **Market probability includes the vig.** `fair_prob` is the implied probability of the
   averaged over price (≈ 52.4% at −110 instead of 50%), so every over edge is understated
   and every under edge overstated by the vig.
4. **Mixed lines.** Consensus averages prices across books offering *different* lines, and
   the "best price" book can be at a different line than the one the model priced.
5. **Averaging American odds is invalid across the ±100 boundary** (e.g. +150 and −110
   average to +20).

## Behavior

### Consensus per (game, player, market) — `betting.market.consensus_offer`

- **Consensus line** = the line offered by the most books; ties → the tied line closest to
  the median of all offered lines, then the lower line (deterministic; three books at three
  different lines resolve to the middle one, not an extreme).
- Only books **at the consensus line** contribute prices and are eligible as best book.
- **Fair over probability** = mean over those books of the proportionally de-vigged over
  probability (`betting.vig.vig_remove_proportional`). Books with no under price use their
  raw over implied probability; `vig_removed` is true only if every contributing book had
  both prices.
- **Best over / best under** = highest American price at the consensus line (ties → book
  name ascending). Best under is `None` when no book at the line has an under price.
- `consensus_price` (kept for compatibility) = American odds of the mean raw over implied
  probability (not the mean of American prices).

### Edge side — `betting.market.pick_side`

- `edge_over = p_model_over − fair_over`; `edge_under = −edge_over`.
- Side = the positive one, edge reported as a **positive magnitude**; book/price come from
  that side's best offer.
- If the under side wins but no under price exists → rejected with
  `rejection_reason = "no_under_price"`.
- Existing sanity bound (±0.50), per-market thresholds and exposure caps apply unchanged
  to the magnitude.

### Storage — migration `006_under_prices.sql` (additive only)

- `odds.under_price INT NULL`
- `odds_normalized`: `best_under_book TEXT`, `best_under_price INT`,
  `vig_removed BOOLEAN NOT NULL DEFAULT FALSE`
- The CSV odds fallback accepts an optional `under_price` column; rows without it keep
  `under_price NULL` (over side only).

## Out of Scope

- Pricing each book's own line separately (alt lines); de-vig methods other than
  proportional; CLV tracking; changes to thresholds/caps.
- ~~Retiring `_ensure_pregame_tables`~~ — done: migration `007_pregame_schema_reconcile.sql`
  now owns that schema and the pipeline only verifies it (`_require_pregame_schema`).

## Acceptance Criteria

1. Pure functions with tests (typical, edge, missing data): consensus line + ties, mixed
   lines excluded, de-vig math, missing under price, best price ties, side selection both
   ways, `no_under_price` rejection.
2. Fake-data smoke test produces under picks when the model leans under, priced at the
   under side's best book.
3. `vig_removed` true for fully two-sided markets; at −110/−110 the fair probability is 0.5.

## Open Decisions for Lincoln

1. Proportional de-vig (simple, standard) vs power/Shin (already in `betting/vig.py`, better
   for lopsided favorites/longshots).
2. `card_caps.max_unders_per_card: 2` in `config/filter_rules.yaml` will now actually bind
   on the dashboard card filter. Keep 2?
