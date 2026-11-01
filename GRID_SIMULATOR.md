# Spot Grid simulator — experimental MVP

This module is **paper-only** and cannot submit orders. It is deliberately separate
from the existing prediction-based paper broker.

## Features
- Arithmetic and geometric price grids
- Virtual USDT and asset inventory
- Crossing-based virtual limit fills
- Replacement sell above a filled buy / buy below a filled sell
- Fees and mark-to-market equity/ROI
- Configurable tick, quantity step and minimum notional

## Example
```python
from decimal import Decimal as D
from crypto_signal_engine.grid import GridConfig, GridEngine
bot = GridEngine(GridConfig(D("90"), D("110"), 4, D("1000"), D("100")))
for price in ["94", "102", "97", "108"]:
    bot.on_price(D(price))
print(bot.snapshot())
```

## Limitations
**Not production ready.** Simulated crossings are assumed to fill at limit
price; order-book queue, partial fills, spread, latency and slippage are not
modeled. Asset and quote reservations are not maintained in separate ledger
accounts. Replacement orders may remain unfillable. This MVP does not implement
all Binance filters, live market feed, durable persistence, stop loss,
take profit, realized grid PNL, or Telegram commands. Backtests using this
simplification must not be treated as evidence of future profitability.

No Railway deployment, live exchange access, API keys or changes to existing
forecast/paper trading execution.
