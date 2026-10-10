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
