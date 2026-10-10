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


### Historical materialization invariants

Historical-compatible materialization must reproduce the live feature semantics as closely as the available source data allows.

- Technical features require **100 closed candles** before they are emitted. Download **18 calendar days** of kline warmup before the requested research period so the 4h timeline has at least 100 closed candles at the first research timestamp.
- The warmup applies only to klines. Spot/futures `aggTrades` are downloaded for the actual research period only.
- Binance-only historical data quality is based on **recent activity**, not file existence. At each 60-second snapshot, Binance spot and Binance futures each count as available only if their latest trade is no older than 60 seconds.
- Historical-compatible v1 requires `market_data_quality == 1.0`; if either expected Binance trade stream is stale/missing, return `no_trade`.
- Aggregate trades are reduced online into one-minute signed-volume/absolute-volume buckets. The 1m/5m/15m CVD and CVD-ratio calculations use those rolling buckets, preserving the 60-second snapshot semantics without rescanning every individual trade for every feature calculation.
- Materialized JSONL must be loaded with its original `dataset_provenance`. A database row with the same symbol/timestamp but a different provenance is a hard conflict and must never be silently overwritten.
- Historical replay must filter features by provenance and use only `HISTORICAL_CANDIDATE_GATES` with `HistoricalCompatiblePredictionEngine`. Do not report historical-compatible predictions under the live candidate names.


### Historical-compatible v1 freeze

`historical_compatible_v1` is frozen before the July-August evaluation.

Its strategy score uses only:
- Binance spot CVD,
- Binance futures CVD,
- matching-horizon trend.

For the 15m profile the active weight is therefore `0.18 + 0.18 + 0.15 = 0.51`, just above the frozen `0.50` minimum-active-weight gate.

The following groups are deliberately excluded from v1 even if those fields later become available in a materialized dataset:
- open interest,
- funding,
- long/short or top-trader crowding,
- taker buy/sell ratio,
- order book,
- liquidations.

Any historical model that adds validated Binance Vision metrics is a separate `historical_compatible_v2` hypothesis and must be defined before inspecting its results. Do not retrofit v1 after seeing July-August or September outcomes.

Historical walk-forward first-touch evaluation must use the locally downloaded Binance Vision spot `aggTrades` archives, not the Binance HTTP API. Monthly stability is attributed by prediction creation month and reported separately from the aggregate leaderboard.

If a materialization bug is fixed and the same provenance must be reloaded, use the provenance-safe loader replace mode. Replacement is allowed only when the existing row has the same `dataset_provenance`; a different provenance at the same symbol/timestamp remains a fatal conflict.


## H4 — Historical-compatible v2 derivatives confirmation

### V1 disposition

The July-August research evaluation of `historical_compatible_v1` is complete and rejected as a promotion candidate.

Observed on the frozen 2026-07-01 through 2026-08-31 research window:

- 62 daily windows,
- 24 active windows,
- 174 trades,
- 3 positive active windows,
- net expectancy `-0.1257%`,
- profit factor `0.157`,
- 38 independent samples,
- bootstrap positive-expectancy rate `0.0%`.

Monthly results were negative in both July and August. Do not retune or reuse `historical_compatible_v1` as though it were still an open research candidate. The September holdout remains untouched.

### V2 pre-registered hypothesis

`historical_compatible_v2` tests whether Binance derivatives participation/crowding data can distinguish meaningful order-flow moves from the weak/noisy flow seen in v1.

The 15m v2 feature groups are frozen before outcome inspection as:

- Binance spot CVD: weight `0.18`,
- Binance futures CVD: weight `0.18`,
- Binance open-interest confirmation: weight `0.15`,
- Binance crowding (global long/short plus top-trader long/short): weight `0.09`,
- Binance taker buy/sell flow: weight `0.05`,
- 15m trend: weight `0.15`.

Total expected active weight is `0.80`. V2 must require all six groups to be available and therefore uses a minimum active weight of `0.80` before score normalization.

To isolate the feature-set hypothesis, keep the v1 candidate structure unchanged for the first v2 experiment:

- horizon: 15 minutes,
- direction: LONG,
- required 15m trend regime: `range`,
- required 15m volatility regime: `high`,
- score threshold: `0.20`,
- take profit: `+0.40%`,
- stop loss: `-0.18%`,
- round-trip research cost: `0.12%`.

Do not change TP/SL, score threshold, regime gates, or costs in the first v2 run. A horizon change or different barrier design is a separate hypothesis/version.

Funding is deliberately excluded from the first v2 metrics experiment because the Binance Vision USD-M `metrics` archive does not establish the same funding-rate timestamp semantics used by the live collector. Funding may be added only as a separately sourced and timestamp-validated later version.

Order book and liquidations remain excluded from historical-compatible v2.

### Metrics timestamp gate

V2 is blocked until the metrics alignment validator returns `PASS`.

The validator must:

- use only archived Binance Vision USD-M `metrics` values and already-persisted live Binance OI,
- test `-5`, `0`, and `+5` minute timestamp shifts,
- compare OI values without consulting prediction outcomes,
- fail on duplicate/boundary archive timestamps,
- require a unique low-error alignment rather than choosing a shift when multiple shifts fit similarly,
- persist the selected observable-time shift as an artifact.

If validation is `AMBIGUOUS` or `FAIL`, do not materialize metrics into strategy features and do not run v2.

### V2 evaluation protocol

After timestamp validation passes, materialize a new provenance (never overwrite v1), run only on the July-August research period, and report aggregate plus monthly stability.

Do not inspect September until the v2 definition and July-August research decision are frozen. If v2 passes the research thresholds, September is opened once as the final historical holdout; otherwise v2 is rejected without consuming September.
