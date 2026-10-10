# Research Plan

This file freezes research hypotheses before evaluating their results. Changes to a hypothesis should be made as a new hypothesis/version rather than rewriting the original after seeing outcomes.

## H1 — 15m long range/high with higher-timeframe context

**Base candidate:** `15m_long_range_high`

**Pre-registered hypothesis:** The candidate performs better when the 1h trend regime is **not `downtrend`**.

**Secondary observation only:** Record the 4h trend/volatility context for the same trades. Do not add a 4h gate until the 1h hypothesis has been evaluated.

**Decision threshold:** Do not accept/reject the hypothesis before at least **20 independent hours** containing eligible observations. Keep the existing candidate rules frozen while collecting this sample.

**Primary comparison:**
- Base candidate results with no higher-timeframe filter.
- Counterfactual results where `trend_regime_1h != "downtrend"`.

Compare at minimum:
- trade count,
- pre-cost expectancy,
- execution cost,
- net expectancy,
- profit factor,
- positive-window ratio,
- bootstrap robustness.

A higher-timeframe filter is useful only if the improvement remains after costs and does not come from collapsing the sample to a tiny number of trades.

## H2 — 1h shadow horizon economics

The 1h horizon is **shadow-only**. It is persisted and evaluated, but must not open paper/candidate positions, affect readiness/promotion, or emit normal trade alerts.

**Question:** Does extending the holding horizon create enough gross movement to overcome the same round-trip execution cost?

Evaluate:
- gross/pre-cost expectancy,
- execution cost,
- net expectancy,
- TP/SL/no-touch distribution,
- profit factor,
- robustness across independent windows.

Do not promote the 1h model until it independently satisfies the normal validation thresholds.

## H3 — Historical backfill protocol

Historical data is used to accelerate research, but it does **not** replace live forward validation.

### Frozen 90-day split

Use complete calendar months so the historical holdout remains separate from the current October 2026 live-forward stream:

- **Pipeline smoke test:** a 7-day slice only for download/runtime/size validation; it is not evidence.
- **Research/development window:** 2026-07-01 00:00 UTC through 2026-08-31 23:59:59 UTC.
- **Final historical holdout:** 2026-09-01 00:00 UTC through 2026-09-30 23:59:59 UTC.
- **Live forward validation:** October 2026 onward; never merge this with historical holdout statistics.

The September holdout must be inspected **once**, after all research choices based on July-August are frozen. If a rule is changed after seeing the holdout, that holdout is considered consumed and cannot be reused as unbiased evidence. The October 8-9 observations that originally highlighted `15m_long_range_high` are discovery data and are not counted as independent validation evidence.

### Primary free sources

Use Binance Public Data / Binance Vision first:

- Spot `aggTrades` for Binance spot CVD.
- USD-M futures `aggTrades` for Binance futures CVD.
- Spot `klines` for 5m / 15m / 1h / 4h technical context.
- USD-M futures `metrics` for Binance OI, long/short, top-trader and taker-ratio research.
- Historical funding data where available.

Bybit public historical trades may later be added as a second-source CVD backfill.

### Feature compatibility rules

Historical proxies must never be silently mixed with live features that have a different definition.

- Binance `bookDepth` is **not** equivalent to the live first-20-level order-book imbalance. If used, store it under a separate feature/model version.
- Missing historical liquidation data must remain unavailable/explicitly missing; do not replace it with zero as though zero liquidations were observed.
- A Binance-only historical model is a separate research profile from the full live Binance+Bybit model.
- The Binance-only profile must also use its own data-quality and source-coverage semantics: one expected Binance spot stream and one expected Binance futures stream count as full historical coverage; they must not inherit the live 6-source quality denominator or the live 2-exchange CVD coverage penalty.
- Historical-compatible v1 removes the single-source CVD coverage penalty and normalizes the final composite score by the sum of active feature weights. This preserves the meaning of the frozen 0.20 score threshold when unavailable live-only feature groups are excluded.
- Historical-compatible v1 requires at least **0.50 active feature weight** before normalization. If active weight is below 0.50, return `no_trade`; do not let a small number of available features become artificially overconfident after normalization.
- Backfilled rows must carry a distinct dataset/model provenance so historical and live results can be separated in reports.

### Metrics timestamp / lookahead rule

Do not assume the Binance USD-M `metrics` timestamp convention from documentation or memory alone.

Before metrics are allowed into strategy evaluation:

- Download the Binance Vision `metrics` files covering **2026-10-08 through 2026-10-10**.
- Align archived Binance OI values against the already persisted live Binance OI series from the same dates.
- Test both zero-shift and five-minute-shift alignment and determine which convention matches the observable live values.
- Normalize historical metric timestamps to the earliest time the value was actually observable.
- Detect duplicate/boundary timestamps explicitly rather than silently de-duplicating them.
- Lock the inferred alignment rule in a regression/lookahead test before running the research or holdout windows.

If the live-vs-archive comparison is ambiguous, exclude `metrics` from strategy evaluation until the timestamp semantics are resolved.

### Candidate research protocol

First evaluate `15m_long_range_high_historical_compatible_v1` on July-August using the historical-compatible model `historical_compatible_v1_15m`, which deliberately excludes exact live order-book imbalance and liquidation flow.

Do **not** alter the frozen live candidate based on historical results. Any modified historical hypothesis becomes a new candidate/version.

Only after the July-August research choices are frozen:

1. Run the untouched rules once on September.
2. Report gross expectancy, execution cost, net expectancy, profit factor, trade count, active windows and robustness.
3. Reject apparent improvements that depend on a very small surviving sample.
4. Continue October live-forward validation regardless of the historical result.

### Paid data escalation

Do not pay for full-depth historical order book or liquidation feeds initially.

Consider Tardis or another tick/L2 vendor only if the free-data experiment shows a stable gross edge and there is a concrete hypothesis that exact L2/liquidation history would materially change the decision.


### Sampling rule

Backfilled research feature snapshots are generated at **60-second cadence**, matching the prediction decision cadence. Do not create 5-second historical feature rows.

Exact first-touch outcome evaluation may still use tick-level aggregate trades. This keeps feature storage manageable without degrading barrier evaluation.

The H1/H2 pre-registered decision thresholds, including the minimum independent-time requirements, apply to historical backfill results as well as forward results.


### Monthly stability reporting

Historical results must be reported both in aggregate and **month by month**. At minimum, print trade count, gross expectancy, execution cost, net expectancy, profit factor, active windows and robustness for each calendar month.

A candidate that is positive only in one month but weak/negative across the other months is not treated as stable evidence of edge.


### Live historical-compatible shadow parity

The historical-compatible model should also run in live shadow mode, but only after live feature snapshots expose **Binance-only** spot/futures CVD ratios and the same Binance-only derivative inputs used by backfill.

Do not run the historical-compatible shadow model on the current aggregate Binance+Bybit CVD ratios and call it equivalent. The live shadow and backfill model definitions must be feature-for-feature identical before their results are compared.
