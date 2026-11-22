"""Bybit BTC/ETH/SOL multi-timeframe signal research. Read-only: never places orders.

Run: PYTHONPATH=src python -m crypto_signal_engine.examples.bybit_signal_v1
"""
import argparse
import asyncio
from datetime import UTC, datetime
from statistics import mean

import aiohttp

from crypto_signal_engine.examples.bybit_momentum_scanner import get
from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.signals.historical_price_forecast import predict_prices, _fit, _estimate
from datetime import timedelta
from decimal import Decimal

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
INTERVALS = ("1", "3", "5", "15")


def ema(values, period):
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def rsi(closes, period=14):
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gains = sum(max(x, 0) for x in changes[-period:])
    losses = sum(max(-x, 0) for x in changes[-period:])
    return 100 * gains / (gains + losses) if gains + losses else 50.0


def atr(rows, period=14):
    ranges = []
    for before, current in zip(rows, rows[1:]):
        previous_close = before["close"]
        ranges.append(max(current["high"] - current["low"],
                          abs(current["high"] - previous_close),
                          abs(current["low"] - previous_close)))
    return mean(ranges[-period:])


def summarize(rows):
    if len(rows) < 60:
        raise ValueError("At least 60 contiguous closed candles required")
    if any(b["start"] - a["start"] != rows[0]["step"]
           for a, b in zip(rows, rows[1:])):
        raise ValueError("Missing candles in history")
    closes = [r["close"] for r in rows]
    volumes = [r["turnover"] for r in rows]
    average = mean(volumes[-21:-1])
    return {
        "price": closes[-1],
        "ema20": ema(closes[-60:], 20),
        "ema50": ema(closes[-60:], 50),
        "rsi": rsi(closes),
        "atr": atr(rows),
        "volume_ratio": volumes[-1] / average if average > 0 else 0,
        "high20": max(r["high"] for r in rows[-21:-1]),
        "low20": min(r["low"] for r in rows[-21:-1]),
        "previous_high20": max(r["high"] for r in rows[-22:-2]),
        "previous_low20": min(r["low"] for r in rows[-22:-2]),
        "last": rows[-1],
        "previous": rows[-2],
    }


def evaluate(frames, spread_pct, equity, risk_pct, cost_pct, max_spread_pct):
    """Separate strategy evidence from leverage and sizing; no execution."""
    f1, f3, f5, f15 = (frames[x] for x in INTERVALS)
    price = f1["price"]
    up = f15["price"] > f15["ema20"] > f15["ema50"]
    down = f15["price"] < f15["ema20"] < f15["ema50"]
    momentum_up = f3["price"] > f3["ema20"] and f3["rsi"] >= 52
    momentum_down = f3["price"] < f3["ema20"] and f3["rsi"] <= 48
    entry_up = f1["price"] > f1["ema20"] and f1["last"]["close"] > f1["last"]["open"]
    entry_down = f1["price"] < f1["ema20"] and f1["last"]["close"] < f1["last"]["open"]
    volume = f5["volume_ratio"] >= 1.3
    trend_long = up and f5["price"] > f5["ema20"] and momentum_up and entry_up and volume
    trend_short = down and f5["price"] < f5["ema20"] and momentum_down and entry_down and volume

    # Breakout confirmed on previous 5m close, then latest closed 5m
    # candle retests the former boundary without closing back through it.
    previous = f5["previous"]
    last = f5["last"]
    retest_long = (up and previous["close"] > f5["previous_high20"]
                   and last["low"] <= f5["previous_high20"]
                   and last["close"] > f5["previous_high20"]
                   and momentum_up and entry_up and volume)
    retest_short = (down and previous["close"] < f5["previous_low20"]
                    and last["high"] >= f5["previous_low20"]
                    and last["close"] < f5["previous_low20"]
                    and momentum_down and entry_down and volume)
    strategies = {
        "TREND": "LONG" if trend_long else "SHORT" if trend_short else "BEKLE",
        "RETEST": "LONG" if retest_long else "SHORT" if retest_short else "BEKLE",
    }
    direction = strategies["RETEST"] if strategies["RETEST"] != "BEKLE" else strategies["TREND"]
    reasons = [
        f"15m trend={'YUKARI' if up else 'ASAGI' if down else 'KARISIK'}",
        f"5m hacim={f5['volume_ratio']:.2f}x",
        f"3m RSI={f3['rsi']:.1f}",
        f"1m teyit={'LONG' if entry_up else 'SHORT' if entry_down else 'YOK'}",
    ]
    if spread_pct is None or spread_pct > max_spread_pct:
        reasons.append("spread uygun degil veya veri yok")
        direction = "BEKLE"
    if direction == "BEKLE":
        return dict(direction=direction, strategies=strategies, reasons=reasons,
                    price=price, leverage=None, entry=None, stop=None, target=None,
                    margin=None, roi=None)
    support = min(f5["low20"], price - 1.5 * f5["atr"])
    resistance = max(f5["high20"], price + 1.5 * f5["atr"])
    stop = support if direction == "LONG" else resistance
    distance = abs(price - stop) / price
    if not 0 < distance <= 0.04:
        reasons.append("teknik stop mesafesi uygun degil")
        direction = "BEKLE"
    # The leverage choice is risk-based, not score-based.
    leverage = 5 if distance <= 0.012 and spread_pct <= 0.04 else 3
    # cost_pct represents total entry+exit costs as % of notional, not margin.
    # ROI on margin = leverage * (underlying return - round-trip cost).
    required_move = 0.10 / leverage + cost_pct / 100
    target = price * (1 + required_move) if direction == "LONG" else price * (1 - required_move)
    reward_risk = required_move / (distance + cost_pct / 100)
    if target <= 0 or reward_risk < 1.5:
        reasons.append(f"hedef/stop orani yetersiz ({reward_risk:.2f})")
        direction = "BEKLE"
    if direction == "BEKLE":
        return dict(direction=direction, strategies=strategies, reasons=reasons,
                    price=price, leverage=None, entry=None, stop=None, target=None,
                    margin=None, roi=None)
    risk_usdt = equity * risk_pct / 100
    notional = risk_usdt / (distance + cost_pct / 100)
    margin = notional / leverage
    if margin > equity * 0.25:
        margin = equity * 0.25
        reasons.append("teminat sermayenin %25'i ile sinirlandi")
    reasons.append(f"hedef/stop={reward_risk:.2f}")
    return dict(direction=direction, strategies=strategies, reasons=reasons,
                price=price, leverage=leverage, entry=price, stop=stop,
                target=target, margin=margin, roi=10.0)


async def fetch_frame(session, symbol, interval, now):
    result = await get(session, "/v5/market/kline", {
        "category": "linear", "symbol": symbol, "interval": interval, "limit": "100"})
    step = int(interval) * 60_000
    cutoff = int(now.timestamp() * 1000) // step * step
    rows = []
    for raw in result.get("list", []):
        start = int(raw[0])
        if start >= cutoff:
            continue
        rows.append({"start": start, "step": step,
                     "open": float(raw[1]), "high": float(raw[2]),
                     "low": float(raw[3]), "close": float(raw[4]),
                     "turnover": float(raw[6])})
    rows.sort(key=lambda row: row["start"])
    return summarize(rows)


async def raw_forecasts(session, symbol, now):
    """Return actual model estimates even when out-of-sample validation fails."""
    end = now.replace(second=0, microsecond=0)
    candles = []
    for page in range(2):
        page_end = end - timedelta(minutes=page * 400)
        page_start = page_end - timedelta(minutes=400)
        result = await get(session, "/v5/market/kline", {
            "category": "linear", "symbol": symbol, "interval": "1",
            "start": int(page_start.timestamp() * 1000),
            "end": int((page_end - timedelta(milliseconds=1)).timestamp() * 1000),
            "limit": "400",
        })
        for row in result.get("list", []):
            opened = datetime.fromtimestamp(int(row[0]) / 1000, UTC)
            if not page_start <= opened < page_end:
                continue
            candles.append(Candle(
                symbol=symbol, interval="1m", open_time=opened,
                close_time=opened + timedelta(minutes=1),
                open=Decimal(row[1]), high=Decimal(row[2]),
                low=Decimal(row[3]), close=Decimal(row[4]),
                volume=Decimal(row[5]), quote_volume=Decimal(row[6])))
    candles = sorted({c.open_time: c for c in candles}.values(), key=lambda c: c.open_time)
    validated = predict_prices(candles, horizons=(5, 15, 30, 50))
    last = len(candles) - 1
    return [
        (prediction, _estimate(candles, last, _fit(candles, prediction.horizon_minutes, last)))
        for prediction in validated
    ]


async def market_flow(session, symbol):
    """Snapshot of recent taker trades and visible order-book liquidity."""
    trades, book = await asyncio.gather(
        get(session, "/v5/market/recent-trade", {
            "category": "linear", "symbol": symbol, "limit": "1000"}),
        get(session, "/v5/market/orderbook", {
            "category": "linear", "symbol": symbol, "limit": "50"}))
    buy = sell = 0.0
    for trade in trades.get("list", []):
        notional = float(trade["price"]) * float(trade["size"])
        if trade["side"] == "Buy":
            buy += notional
        elif trade["side"] == "Sell":
            sell += notional
    total = buy + sell
    if total <= 0:
        raise ValueError("Recent trade volume unavailable")
    bids = [(float(price), float(qty)) for price, qty in book.get("b", [])]
    asks = [(float(price), float(qty)) for price, qty in book.get("a", [])]
    if not bids or not asks:
        raise ValueError("Order book unavailable")
    mid = (bids[0][0] + asks[0][0]) / 2
    # Only near-mid liquidity; deeper orders are not treated as immediate pressure.
    bid_usdt = sum(price * qty for price, qty in bids if price >= mid * 0.995)
    ask_usdt = sum(price * qty for price, qty in asks if price <= mid * 1.005)
    if bid_usdt + ask_usdt <= 0:
        raise ValueError("Order book near-mid liquidity unavailable")
    return {
        "buy_pct": 100 * buy / total,
        "sell_pct": 100 * sell / total,
        "bid_pct": 100 * bid_usdt / (bid_usdt + ask_usdt),
        "ask_pct": 100 * ask_usdt / (bid_usdt + ask_usdt),
    }


def early_view(frames, flow, forecasts):
    """Directional research view, not an order-entry recommendation."""
    f1, f3, f5, f15 = (frames[x] for x in INTERVALS)
    score = 0
    reasons = []
    trend = 1 if f15["price"] > f15["ema20"] > f15["ema50"] else (
        -1 if f15["price"] < f15["ema20"] < f15["ema50"] else 0)
    score += 2 * trend
    reasons.append(f"15dk trend={trend:+d}")
    momentum = 1 if f3["rsi"] >= 55 else -1 if f3["rsi"] <= 45 else 0
    score += momentum
    reasons.append(f"3dk RSI={f3['rsi']:.1f}")
    candle = 1 if f1["last"]["close"] > f1["last"]["open"] else (
        -1 if f1["last"]["close"] < f1["last"]["open"] else 0)
    score += candle
    reasons.append(f"son 1dk mum={candle:+d}")
    trade_side = 1 if flow["buy_pct"] >= 55 else -1 if flow["sell_pct"] >= 55 else 0
    score += 2 * trade_side
    reasons.append(f"agresif alis=%{flow['buy_pct']:.1f}")
    book_side = 1 if flow["bid_pct"] >= 55 else -1 if flow["ask_pct"] >= 55 else 0
    score += book_side
    reasons.append(f"yakin emir defteri alis=%{flow['bid_pct']:.1f}")
    # Model is a weak directional input, never the sole deciding factor.
    forecast_changes = [(estimate / float(pred.current) - 1) * 100
                        for pred, estimate in forecasts]
    forecast_side = 1 if all(x > 0 for x in forecast_changes) else (
        -1 if all(x < 0 for x in forecast_changes) else 0)
    score += forecast_side
    reasons.append(f"model yon={forecast_side:+d}")
    label = "ERKEN LONG ADAYI" if score >= 3 else (
        "ERKEN SHORT ADAYI" if score <= -3 else "KARISIK / BEKLE")
    return label, score, reasons


async def scan_symbol(session, symbol, now, args):
    frames = dict(zip(INTERVALS, await asyncio.gather(*(
        fetch_frame(session, symbol, interval, now) for interval in INTERVALS))))
    ticker = await get(session, "/v5/market/tickers", {
        "category": "linear", "symbol": symbol})
    item = ticker["list"][0]
    bid, ask = float(item["bid1Price"]), float(item["ask1Price"])
    spread = (ask - bid) / ((ask + bid) / 2) * 100 if bid > 0 and ask >= bid else None
    result = evaluate(frames, spread, args.equity, args.risk_pct,
                      args.cost_pct, args.max_spread_pct)
    result["frames"] = frames
    return result


async def run(args):
    now = datetime.now(UTC)
    print(f"BYBIT AI SIGNAL BOT V1 | {now:%Y-%m-%d %H:%M} UTC | SADECE SINYAL")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
        for symbol in SYMBOLS:
            try:
                result = await scan_symbol(session, symbol, now, args)
                print(f"\n{symbol} | {result['direction']} | fiyat={result['price']:.8g}")
                print("  Stratejiler: " + " | ".join(
                    f"{key}={value}" for key, value in result["strategies"].items()))
                if result["direction"] != "BEKLE":
                    print(f"  Giris={result['entry']:.8g} | TP={result['target']:.8g} "
                          f"| SL={result['stop']:.8g} | {result['leverage']}x "
                          f"| teminat={result['margin']:.2f} USDT | hedef net ROI~%10")
                print("  Neden: " + "; ".join(result["reasons"]))
                try:
                    flow = await market_flow(session, symbol)
                    print(f"  Islemler: agresif alis=%{flow['buy_pct']:.1f} "
                          f"| agresif satis=%{flow['sell_pct']:.1f} "
                          f"| emir defteri yakin alis=%{flow['bid_pct']:.1f} "
                          f"| satis=%{flow['ask_pct']:.1f}")
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError,
                        TypeError, RuntimeError, IndexError, ZeroDivisionError) as exc:
                    flow = None
                    print(f"  Alis/satis verisi alinamadi: {exc}")
                try:
                    forecasts = await raw_forecasts(session, symbol, now)
                    for forecast, estimate in forecasts:
                        change = (estimate / float(forecast.current) - 1) * 100
                        label = "MODEL BAZDAN IYI" if forecast.beats_naive else "MODEL ZAYIF"
                        tendency = "YUKARI" if change > 0 else "ASAGI" if change < 0 else "YATAY"
                        print(f"  {forecast.horizon_minutes}dk model tahmini={estimate:.8g} USDT "
                              f"| degisim={change:+.3f}% | yon={tendency} | {label} "
                              f"| model hata={float(forecast.model_mae_pct):.3f}% "
                              f"| baz hata={float(forecast.naive_mae_pct):.3f}%")
                    if flow is not None:
                        label, score, evidence = early_view(result["frames"], flow, forecasts)
                        print(f"  Erken piyasa gorunumu: {label} | puan={score:+d}/8")
                        print("  Kanit: " + "; ".join(evidence))
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError,
                        KeyError, ZeroDivisionError, IndexError, TypeError) as exc:
                    print(f"  5/15/30/50dk fiyat tahmini hesaplanamadi: {exc}")
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError,
                    TypeError, RuntimeError, IndexError, ZeroDivisionError) as exc:
                print(f"\n{symbol} | BEKLE | veri hatasi: {exc}")
    print("\nHedefler varsayimsal maliyetlerle hesaplanir; backtest edilmemistir. Emir gonderilmez.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--equity", type=float, default=1000, help="Example account equity USDT")
    parser.add_argument("--risk-pct", type=float, default=1.0, help="Equity risk per signal (%)")
    parser.add_argument("--cost-pct", type=float, default=0.15,
                        help="Estimated round-trip fees+slippage as % of notional")
    parser.add_argument("--max-spread-pct", type=float, default=0.10)
    args = parser.parse_args()
    if args.equity <= 0 or not 0 < args.risk_pct <= 2 or not 0 <= args.cost_pct <= 2:
        parser.error("Invalid risk parameters")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
