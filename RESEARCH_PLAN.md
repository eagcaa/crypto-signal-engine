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
- Materialized JSONL must be loaded with its original `dataset_provenance`. Research feature identity is `(timestamp, symbol, dataset_provenance)`, so multiple model/data versions may coexist at the same market timestamp without overwriting one another.
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

If a materialization bug is fixed and the same provenance must be reloaded, use the provenance-safe loader replace mode. Replacement only affects the matching `(timestamp, symbol, dataset_provenance)` row; other provenances at the same timestamp remain untouched.


## H4 — V2 selection protocol after v1 rejection

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

The 2026-04-01 through 2026-06-30 period is reserved as a secondary backup holdout. Do not inspect or optimize against it during V2 research.

### Research-only feature diagnostics before freezing V2

Before freezing a V2 strategy, use the July-August development data to diagnose whether the existing feature families contain directional information at 15m, 1h or 4h.

The diagnostics are exploratory and may inspect only the July-August research window. They must refuse any input containing rows at or after 2026-09-01.

For each feature/horizon pair report at minimum:

- Spearman rank correlation with forward return,
- top-decile mean forward return,
- bottom-decile mean forward return,
- top-minus-bottom spread,
- first-half spread,
- second-half spread,
- whether the spread sign is stable across halves,
- whether the implied one-sided spread is large enough to clear the frozen round-trip research cost.

A feature/horizon relationship is not treated as useful merely because the full-period spread is large. The sign must also be stable across the two development halves.

If the existing spot/futures CVD or trend features show a stable cost-clearing relationship at a longer horizon, the first V2 hypothesis may be a horizon change rather than a larger feature set.

If none of the existing feature families show a stable relationship at any tested horizon, V2 research moves to new derivative inputs.

### July-August diagnostics result

The first feature diagnostics pass is complete on the 2026-07-01 through 2026-08-31 research window.

- None of the tested existing feature/horizon pairs (spot CVD, futures CVD or trend at 15m, 1h or 4h) cleared the frozen 0.12% round-trip research cost.
- Rank correlations were weak (approximately within +/-0.05).
- Spot 15m CVD showed a small but stable negative top-minus-bottom spread across 15m, 1h and 4h forward horizons, consistent with weak mean reversion rather than the directional interpretation used by v1.
- The magnitude of this effect is far below trading cost and is not itself a strategy hypothesis.
- Trend features were not stable across the two development halves and are treated as regime description, not demonstrated directional edge.

This closes the "v1 only needs a longer horizon" explanation for the current feature set. Any V2 hypothesis must add independently justified information rather than simply stretching the same signal.

### Metrics timestamp / observability gate

Before Binance Vision USD-M `metrics` fields are eligible for feature diagnostics or strategy evaluation, their observable-time semantics must be validated against already-persisted live Binance derivatives snapshots.

Run two complementary checks:

1. Open-interest shift scan over a bounded set of minute shifts.
2. Ratio first-seen analysis that compares every archived ratio column against the persisted live long/short, top-trader and taker-ratio fields.

The ratio analysis must infer the archive-to-live mapping from observed values rather than assuming it in advance.

Report duplicate archive timestamps explicitly. A metrics row labelled T may be exposed to historical features only at or after the conservatively inferred observable time.

The October live/archive comparison found a clear open-interest alignment: the archived OI row labelled T best matched the live OI observed around T + 5 minutes. Historical OI research therefore uses T + 6 minutes as the earliest observable time, adding a one-minute safety buffer.

The archived long/short, top-trader and taker-ratio fields did not achieve sufficiently confident one-to-one matching against the corresponding persisted live API values. They remain excluded.

The validator distinguishes these cases:

- `PASS`: OI and ratio observability are validated.
- `PASS_OI_ONLY`: OI observability is validated, but ratio fields are not.
- `FAIL` / `AMBIGUOUS`: OI itself is not safe to use.

A `PASS_OI_ONLY` artifact may be used only for OI diagnostics. It must not unlock the full historical V2 materializer or expose crowding/taker fields.

### Pre-registered OI diagnostic set

The OI diagnostics test exactly these four features at 15m, 1h and 4h forward horizons (12 pairs), using the T + 6 minute observability rule:

- `binance_oi_change_5m_pct`, `binance_oi_change_15m_pct`: USD OI value change (partly contains the price move itself).
- `binance_oi_contracts_change_5m_pct`, `binance_oi_contracts_change_15m_pct`: contract-quantity OI change, free of the price component.

No further OI transformations may be added after viewing these results without recording a new hypothesis here first. The 4h horizon has only ~370 non-overlapping periods in July-August and is weaker evidence than 15m or 1h.

### OI diagnostic result

The pre-registered OI diagnostic set is complete on the 2026-07-01 through 2026-08-31 research window using the T + 6 minute observability rule.

None of the 12 pre-registered OI feature/horizon pairs cleared the frozen 0.12% round-trip research cost.

Key observations:

- USD OI 5m change showed stable positive spreads at 1h and 4h, but the spreads were only 0.0310% and 0.0768%.
- USD OI 15m change showed a stable 1h spread of 0.0368%; its 4h split was not stable.
- Contract-quantity OI 5m change showed stable positive spreads at 1h and 4h of 0.0339% and 0.0658%.
- Contract-quantity OI 15m change was the strongest OI feature, with stable positive spreads of 0.0515% at 1h and 0.0924% at 4h.
- Even the strongest observed spread implies only about 0.0462% one-sided separation under the diagnostic heuristic, well below the 0.12% round-trip cost.
- The 4h result is weaker evidence than the minute-row sample count suggests because July-August contains only roughly 370 non-overlapping 4h periods.

Therefore validated OI does not provide a cost-clearing directional edge under the pre-registered transforms and horizons. Do not build or evaluate a V2 strategy by combining these weak OI effects with the already weak CVD/trend effects. Any further OI transform, interaction, threshold, regime filter or horizon is a new hypothesis and must be pre-registered before testing.

September remains untouched.

### Rejected hypothesis family

**Rejected hypothesis family:** BTCUSDT, Binance free flow and OI data, 15-minute to 4-hour directional prediction. No feature cleared trading costs in the July-August diagnostics. September and April-June were not used.

> **Superseded:** V2 was not built. The directional hypothesis family was rejected above; the rules below are kept only as a historical record.

### V2 freeze rule

Do not select V2 features, horizon, score threshold, regime gates or barriers by looking at September.

V2 is frozen only after:

1. July-August diagnostics for the existing feature set are complete.
2. Metrics observability validation is complete if metrics are being considered.
3. Validated OI is run through the same July-August 15m/1h/4h feature diagnostics using the T + 6 minute observability rule.
4. Only metrics fields that independently pass observability validation may be considered for a strategy hypothesis.
5. The V2 hypothesis is written here before any September result is inspected.

The existing `HistoricalCompatibleV2PredictionEngine` implementation is provisional research scaffolding only until this freeze step is completed. It must not be treated as the final V2 hypothesis merely because the code exists.

> **Superseded:** V2 was not built. The directional hypothesis family was rejected above; the rules below are kept only as a historical record.

### V2 evaluation protocol

After the V2 hypothesis is frozen, evaluate it on July-August and report aggregate plus monthly stability.

Only if it passes the research thresholds should September be opened once as the primary final historical holdout. If V2 is changed after viewing September, September is consumed and cannot be reused as unbiased evidence; use the untouched April-June backup holdout for a later version instead.

October live-forward data remains separate from all historical holdouts.


## H5 — Delta-neutral funding/basis research

### Pre-registered hypothesis

BTCUSDT spot long plus an equal-notional Binance USD-M perpetual short may earn a positive net return from funding/basis carry without relying on BTC price direction.

**Why this may clear costs:** the return source is repeated carry while the position remains open, so trading costs are paid infrequently relative to the holding period rather than on every short-horizon directional prediction.

This is a separate hypothesis family from H1-H4. The September 2026 and April-June 2026 directional holdouts are not reused for H5.

### Frozen research periods

- **Development:** 2020-01-01 through 2023-12-31.
- **Final test:** 2024-01-01 through 2025-12-31, inspected once only after the development rules are frozen.
- Do not tune H5 on the final-test period. Any rule changed after viewing 2024-2025 consumes that test period for the changed version.

### Baseline first: always-on carry

The first H5 experiment is the simplest possible version: maintain an equal-notional spot-long / perpetual-short position continuously, with no funding threshold, volatility filter, trend filter, weekday filter, regime filter or discretionary entry rule.

Do not add conditional entry/exit rules unless the always-on development result is economically viable after the frozen cost assumptions. If the always-on baseline does not clear costs, any later filtered version is a new hypothesis and must be pre-registered before testing.

### Frozen research cost assumptions

For the first research pass, model a conservative standard-fee case:

- Spot fee: 0.10% per side.
- Futures fee: 0.05% per side.
- One complete entry plus exit across both legs therefore costs 0.30% before slippage.
- Report slippage separately and also report a conservative net result including an explicit slippage assumption.

These are research assumptions, not a claim about the user's account-specific Binance fee tier. Before any live implementation, replace them only with verified account/exchange fees and record the change as a new execution assumption.

### Rebalancing rule (always-on baseline)

When the futures margin buffer falls below 50% of its initial allocation, sell enough spot BTC to restore the futures margin buffer to its initial level and reduce the perpetual short by the same BTC quantity so the position remains delta-neutral.

All rebalancing executions pay the frozen spot and futures fees plus the frozen slippage assumption. Report the number of rebalances, BTC/notional resized, capital transferred to futures margin, and total rebalancing cost.

The 50% trigger is frozen before viewing development results and must not be changed after results are inspected. A different trigger is a new hypothesis.

### Slippage and cost sensitivity

Freeze slippage at **0.02% per execution per leg** for the detailed baseline simulation.

Under the frozen standard-fee assumptions, the initial complete entry plus exit costs 0.30% in fees before slippage. Four executions across spot and futures entry/exit add 0.08% slippage, for a detailed baseline round-trip assumption of approximately **0.38%**, before any rebalancing costs.

In addition to the detailed execution accounting, report sensitivity at three simplified **all-in complete round-trip cost** levels:

- 0.20%
- 0.30%
- 0.40%

These sensitivity cases are alternative all-in scenarios and must not be added on top of the detailed fee/slippage calculation.

### Capital-return definition and benchmark

Measure return on **total capital committed**, not on gross notional alone. Total committed capital includes the capital used for the full spot leg plus the futures margin allocated to the short leg.

Report at minimum:

- cumulative net return on committed capital,
- annualized net return,
- funding income before costs,
- total trading costs,
- maximum drawdown,
- worst calendar month,
- longest negative-funding streak,
- fraction of time funding is negative,
- turnover / number of complete position cycles,
- return by calendar year.

Benchmark the strategy against the **US 3-month Treasury bill rate, FRED series DTB3**, over the same period. Use the historical observations from FRED and compound the benchmark through time using a documented, deterministic conversion from the quoted annualized rate to the simulation time step; do not select or change the benchmark after viewing strategy results.

Report benchmark cumulative return and annualized return. Excess return = strategy annualized net return minus benchmark annualized return.

The strategy is not considered economically attractive merely because net return is positive; the excess return must be large enough to justify exchange, margin, operational and liquidation risks.

### H5 hourly price timestamp semantics

For H5 spot and perpetual 1h Binance Vision klines, historical price availability is timestamped at the **scheduled end of the hour derived from kline open time** (`open_time + 1h - 1ms`), not at the archive's raw `close_time` field.

This is conservative for partial candles created around exchange maintenance: a candle that stopped trading early is not exposed to the simulation before the normal end of its hour. Off-grid raw close timestamps therefore do not count as separate hourly observations or as missing-hour replacements.

### H5 development data-quality rule

The H5 baseline may use the latest previously observed spot price across short missing hourly spot-kline gaps only when all of the following are true:

- total missing spot hours are **strictly less than 0.1%** of the expected development-hour grid (at 35,064 expected hours, this permits at most 35 missing hours),
- no contiguous missing spot-price gap exceeds **6 hours**,
- there are no unexpected off-grid spot timestamps,
- spot timestamps contain no duplicates,
- the perpetual hourly series remains complete and duplicate-free.

Every missing spot range must be reported before strategy evaluation. If any condition fails, the H5 data audit remains FAIL and the baseline must not run until a new data-source/remediation rule is explicitly recorded.

This rule was added after the first coverage audit revealed 39 missing spot hours. Because 39 exceeds the frozen <0.1% threshold, this rule does **not** convert that observed dataset to PASS and must not be loosened to rescue the current result.

### No-lookahead funding rule

At every historical decision time, only funding payments whose actual funding timestamps are at or before that time may be used.

Do not use the next announced or predicted funding rate in the first H5 research pass, even if such a value may be visible in live APIs. Use actual historical funding timestamps rather than assuming a fixed 8-hour interval.

### Futures leverage and liquidation stress rule

Model the futures short at both 1x and 2x leverage.

For each leverage case:

- track allocated futures margin separately from spot capital,
- report the minimum margin buffer,
- evaluate whether the allocated margin would survive the worst observed 24-hour adverse BTC price move in the development period,
- treat this as a stress test, not as a substitute for exchange-specific liquidation mechanics.

Before any live use, liquidation distance must be modeled with the exchange's actual mark-price, maintenance-margin and liquidation rules.

### Development decision rule

The always-on development baseline may advance to a frozen H5 test only if it is positive after trading costs and slippage, survives the 1x/2x margin stress report, and shows economically meaningful excess return over the benchmark rather than a trivial positive carry.

"Economically meaningful" is frozen as: annualized excess return over DTB3 of at least **3 percentage points at 1x leverage** under the detailed **0.38% baseline cost accounting**, and positive excess return in at least **3 of the 4 development calendar years (2020-2023)**.

If it does not meet those conditions, stop the H5 baseline. Do not rescue it with filters selected from the same development results without first recording a new hypothesis.

### Time boundary

The H5 research branch should be accepted, rejected or explicitly paused by 2026-12-31. Do not extend research indefinitely by adding post-hoc filters.


### H5 development result — rejected baseline

The frozen H5 always-on development baseline was evaluated once on 2020-01-01 through 2023-12-31 after the H5 data audit passed. The 2024-2025 final-test period remains untouched.

Primary frozen result: **1x leverage, detailed 0.38% baseline cost accounting**.

- cumulative net return: **36.6352%**
- annualized net return: **8.1162%**
- DTB3 annualized benchmark: **1.9169%**
- annualized excess return: **+6.1994 percentage points**
- maximum drawdown: **1.6117%**
- rebalances: **5**
- negative funding fraction: **13.44%**
- longest negative-funding streak: **192 hours**
- worst observed 24h perpetual rise: **34.8865%**
- static 1x margin stress: **survived**

Calendar-year excess return versus DTB3:

- 2020: **+10.7463 pp**
- 2021: **+18.9572 pp**
- 2022: **-0.8874 pp**
- 2023: **-3.0648 pp**

The pre-registered decision rule required both at least **+3 pp annualized excess return** and positive excess return in at least **3 of 4 development calendar years**. H5 passed the aggregate excess-return hurdle but passed only **2 of 4** calendar years. Therefore the frozen always-on H5 baseline is **REJECTED**.

Cost sensitivity did not change the disposition: the 0.20%, 0.30% and 0.40% all-in sensitivity cases showed the same 2020-2021 positive / 2022-2023 negative excess-return pattern. The 2x leverage cases also failed the same year-stability interpretation and do not rescue the baseline.

Two observed explanations are recorded for future hypothesis design, not as retroactive changes to H5:

1. **Economic regime dependence.** Funding carry was strong relative to near-zero DTB3 in 2020-2021, while 2022-2023 combined weaker carry with a much higher USD cash benchmark. The baseline therefore did not demonstrate benchmark-beating stability across regimes.
2. **One-way rebalance shrinkage.** The frozen rebalance rule only reduced the spot/perpetual position when futures margin was stressed. It never rebuilt the position after price declines or excess margin returned. This mechanically reduced later funding exposure and may have contributed materially to the weak 2022-2023 returns. This is an interpretation of the observed development result, not a proven causal decomposition.

Do **not** change the H5 rebalance rule and rerun 2020-2023 as though it were new validation evidence. Any bidirectional rebalance design is a separate hypothesis/version. A development rerun may be used only for implementation verification and must not be counted as independent evidence.

Do **not** inspect 2024-2025 until any successor hypothesis is fully pre-registered and frozen.


## H5b — Two-sided rebalanced carry (pre-registered)

**Status: FROZEN.** Pre-registered on 2026-10-11, after viewing the H5 development result and before any 2024-2025 data was downloaded or inspected. Changing any rule below after this commit consumes the final-test period for the changed version.

### Hypothesis

The H5 always-on spot-long / perpetual-short carry, with its hedge size maintained in both directions by the mechanical rule below, earns economically meaningful excess return over DTB3 in 2024-2025.

H5b was designed after seeing the H5 2020-2023 result. 2020-2023 therefore cannot be evidence for H5b. The only evidential test is the 2024-2025 final-test period, run once.

### What is unchanged from H5

Everything not defined in this section is identical to H5: BTCUSDT, Binance spot and Binance USD-M perpetual, hourly 1h-kline timeline with scheduled-close timestamp semantics, actual funding timestamps with no lookahead, funding paid on `q * latest perp close at or before the funding time`, the frozen fees (spot 0.10%, futures 0.05% per side), slippage 0.02% per execution per leg, detailed 0.38% accounting plus the 0.20/0.30/0.40% sensitivity cases, initial spot notional 10,000 USDT, return on total committed capital, DTB3 benchmark and its conversion, and the static stress test.

No signal or filter is added. There is no entry/exit or sizing decision based on funding, basis, trend, volatility, interest rates or any other market variable. The position is opened at the first hour of the period and held until the last hour.

### Definitions

At each timeline timestamp `t`:

- `S_t`, `P_t`: latest spot and perpetual hourly closes available at `t`.
- `L`: leverage, 1 (primary) or 2 (report only).
- `fs`, `ff`: spot and futures execution cost rates of the cost model (fee + slippage).
- `q_t`: BTC held in spot, always equal to the BTC quantity of the short.
- `E_t`: average entry price of the short.
- `C_t`: futures margin cash (initial margin, plus funding, plus realized short PnL, plus transfers, minus execution costs booked to the margin-cash account).
- `M_t = C_t + q_t * (E_t - P_t)`: futures margin equity.
- `R_t = q_t * P_t / L`: target margin for the current short.
- `m_t = M_t / R_t`: margin coverage ratio.
- `q_0 = 10,000 / S_0` at the first hour of the period: the **BTC quantity cap**. `q_t` never exceeds `q_0`.

At entry: `q = q_0`, `E = P_0`, and initial futures margin is `q_0 * P_0 / L`. As in H5 accounting, both the spot and futures entry execution costs are charged against committed capital and booked to margin cash. Therefore initial `m` is slightly below 1 because of entry costs; no immediate rebalance is performed solely because of those entry costs.

### Rebalancing rule

Order at each timestamp: (1) apply funding events at that timestamp, (2) mark `M_t`, (3) evaluate the triggers, (4) execute at `S_t` and `P_t` when both legs are tradable under the data rule below, (5) record equity. At most one resize per timestamp.

**Down-resize (margin stress):** if `m_t < 0.50`, sell `D` BTC of spot and buy back `D` BTC of the short, moving the spot proceeds to margin, so that coverage returns to exactly 1 after costs:

`D = (R_t - M_t) / (S_t * (1 - fs) - P_t * ff + P_t / L)`

**Up-resize (excess margin):** if `m_t > 1.50` and `q_t < q_0`, move cash from margin to spot, buy `U` BTC of spot and sell `U` BTC more of the short, so that coverage returns to exactly 1 after costs, but never above the cap:

`U = min((M_t - R_t) / (S_t * (1 + fs) + P_t * ff + P_t / L), q_0 - q_t)`

After an up-resize the short average entry price becomes `E' = (q_t * E_t + U * P_t) / (q_t + U)`. If the cap binds, coverage stays above 1 and the remaining excess stays in margin. A down-resize leaves the average entry price of the remaining short unchanged.

If `q_t = q_0` and `m_t > 1.50`, nothing happens. The position never grows above its starting BTC size, so there is no compounding or martingale-style scaling.

**Difference from H5 (intentional):** H5 triggered on margin equity below 50% of the *initial USD margin* and restored it to that USD amount. H5b triggers on and restores the coverage ratio against the *current* short notional. An absolute-USD target cannot be used with up-resizing, because rebuilding size at a fixed USD margin would leave the larger short under-collateralized. Consequence: at 1x the down-resize fires after roughly a 33% perpetual rise from the last reset (H5: ~50%), so H5b may rebalance more often and pay more costs. This is accepted before viewing any result.

The 0.50 and 1.50 thresholds, the coverage target of 1, and the `q_0` BTC cap are frozen.

### Missing-spot execution rule

The existing H5 data-quality rule still permits short spot-data gaps within its frozen limits. During a timestamp for which no actual spot 1h candle exists, the latest prior spot close may be used only to mark portfolio equity. **No resize may execute at a missing-spot timestamp**, because the historical data does not establish a contemporaneous executable spot price there.

Funding may still be applied using the perpetual data at that timestamp. Any pending resize condition is simply re-evaluated at the first later timestamp with an actual spot candle. No synthetic spot fill, forward-filled execution price or perpetual-price substitute is allowed.

### Return accounting

Calendar-year return = year-end equity / previous year-end equity - 1, with the first year starting from committed capital before entry costs. **The final calendar year includes the full exit costs on both legs** (H5 excluded them from its last yearly figure; H5b fixes this conservatively). Annualized return, excess over DTB3 and the DTB3 conversion are as in H5.

### Data

- Final-test period: 2024-01-01 through 2025-12-31 (17,544 expected hours).
- Download command for this period may be written only after this section is committed. FRED DTB3 is fetched from 2023-12-01 to 2025-12-31.
- The H5 data-quality rule applies unchanged on the 2024-2025 grid: fewer than 0.1% missing spot hours (at most 17), no contiguous spot gap above 6 hours, no off-grid or duplicate spot timestamps, perpetual and funding complete. The audit must PASS before the strategy is run; any remediation rule must be recorded before the strategy is run.
- Binance Vision spot archives use microsecond timestamps from 2025-01-01; a test covering both millisecond and microsecond units must exist before the download.

### Verification before the final run

Unit tests must cover at minimum:

- down-resize returns `m` to 1 after costs,
- up-resize returns `m` to 1 after costs,
- the `q_0` cap binds,
- no up-resize at `q = q_0`,
- the average-entry-price update,
- final-year exit costs,
- no resize executes on a missing-spot timestamp,
- millisecond and microsecond Binance timestamps normalize correctly.

Running H5b on 2020-2023 is allowed only to check mechanics. Its output is labelled non-evidential, is recorded as such, and cannot change any H5b rule. Unit and synthetic tests are preferred; a 2020-2023 mechanics run is not required.

### Final-test decision rule (run once)

Primary case: **1x, detailed 0.38% accounting, 2024-01-01 through 2025-12-31.** H5b PASSES only if all hold:

- net return over the period > 0,
- excess return over DTB3 > 0 in **2024** and in **2025**,
- annualized excess return over DTB3 across 2024-2025 >= **+3 percentage points**,
- static stress test survives.

2x and the 0.20/0.30/0.40% sensitivity cases are reported but cannot turn a FAIL into a PASS.

The full command output is committed verbatim. Once primary H5b result metrics for 2024-2025 are produced, the final-test period is consumed for H5b and the test is not re-run after any code or rule change.

If H5b FAILS, H5b is rejected and the 2024-2025 final-test period is consumed for this hypothesis family version. The broader project does not automatically close; any later funding/basis idea must be pre-registered as a new hypothesis and evaluated only on genuinely new, untouched data rather than reusing 2024-2025. If H5b PASSES, the next steps are exchange-accurate liquidation modelling (mark price, maintenance margin, intra-hour moves) and small-size paper trading, not live capital.
