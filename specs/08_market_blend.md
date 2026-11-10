# Spec: Model / Market Blend

**Modules:** `betting/blend.py` (pure), `orchestration/blend_weights.py` (YAML I/O),
`orchestration/pregame.py` (live), `orchestration/backtest.py` (`fit-blend` mode)
**Config:** `config/blend_weights.yaml`, setting `BLEND_WEIGHTS_PATH`
**Owner:** Claude (Lincoln approves)
**Depends on:** specs 05, 06, 07
**Status:** Draft — implemented; the live default weight is an unfitted placeholder

---

## Why

Spec 07's fake-data backtest showed baseline_v1 claims large edges that are mostly
projection noise: against a perfect book it claimed 17.9% average edge and won 53.4%
(≈ break-even). ARCHITECTURE §7 already calls for a calibration step; this is the
market-aware version of it.

## Behavior

- `blend_prob(p_model, p_market, w)`: `logit(p) = w·logit(p_model) + (1−w)·logit(p_market)`,
  `p_market` = spec-06 de-vigged consensus over probability. Log-odds space keeps results in
  (0, 1) and treats 0.50→0.55 and 0.90→0.95 sensibly. `w ∈ [0, 1]`.
- Live: `_run_baseline_model` scores raw baseline_v1, then blends with the per-market weight
  (fallback `default`). Recorded model version: `baseline_v1_blend`.
- Weights file (`config/blend_weights.yaml`): `weights: {default: w, <market>: w, ...}`,
  `fitted: bool`. Loader rejects files without `default` or with weights outside [0, 1].

## Fitting (`python -m orchestration.backtest --mode fit-blend`)

- Samples: every settled closing-line market in the range (all markets, not just picks):
  raw model P(over), de-vigged market P(over), outcome. Voids and pushes excluded.
- Per market: grid search `w ∈ {0.00, 0.01, …, 1.00}` minimizing log loss; ties go to the
  smaller weight (trust the market). Markets with fewer than `MIN_BLEND_FIT_SAMPLES = 200`
  samples use the pooled `default`.
- Writes `data/backtest/blend_weights_<label>.yaml` with weights and diagnostics (market,
  model, blend log loss). **Not** written to `config/` — Lincoln reviews and copies it.
- Always fit on an earlier window and evaluate betting on a later one (out of sample):
  `--mode betting --blend-weights <fitted file>`; `--no-blend` gives the raw model.

## Verification (fake data, 2026-11-10; fit on days 45–67, bet days 68–89)

| Fake book | Fitted w | Bets raw → blend | Avg claimed edge | Avg model p → win rate |
|---|---|---|---|---|
| Noisy (last-10 avg) | 1.0 all markets | 264 → 264 | 12.9% → 12.9% | 0.63 → 71% |
| Perfect (true mean) | pts 0.00, reb 0.23, ast 0.25 | 264 → 42 | 18.3% → 6.1% | raw 0.68 → 54%; blend 0.56 → 65% (n=42) |

The fit keeps the model where it truly adds information and collapses toward the market
where it doesn't.

## Out of Scope

- Re-deriving edge thresholds from realized ROI by bucket (needs real closing-line volume).
- Separate weights by line distance, player role or season phase.

## Open Decisions for Lincoln

1. **Live default weight.** `config/blend_weights.yaml` ships `default: 0.25`, an unfitted
   placeholder chosen to be conservative (it shrinks claimed edges about 4×, so most current
   picks fall under the thresholds). Replace with a fitted file once enough closing lines
   exist; or set 0.0 (no picks) until then?
2. `MIN_BLEND_FIT_SAMPLES = 200` per market.
