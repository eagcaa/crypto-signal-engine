"""Bybit BTC/ETH/SOL multi-timeframe signal research. Read-only: never places orders.

Run: PYTHONPATH=src python -m crypto_signal_engine.examples.bybit_signal_v1
"""
import argparse
import asyncio

from crypto_signal_engine.examples.bybit_prediction_tracker import connect, record, settle, report
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
    records = []
    for trade in trades.get("list", []):
        timestamp = int(trade["time"])
        notional = float(trade["price"]) * float(trade["size"])
        records.append((timestamp, trade["side"], notional))
    if not records:
        raise ValueError("Recent trade volume unavailable")
    newest = max(x[0] for x in records)
    oldest = min(x[0] for x in records)
    buy = sum(x[2] for x in records if x[1] == "Buy")
    sell = sum(x[2] for x in records if x[1] == "Sell")
    total = buy + sell
    if total <= 0:
        raise ValueError("Recent trade volume unavailable")
    windows = {}
    for minutes in (1, 3, 5, 15):
        cutoff = newest - minutes * 60_000
        # Recent-trade API is capped at 1000; avoid claiming full windows
        # if the oldest available trade is newer than the window start.
        complete = oldest <= cutoff
        sample = [x for x in records if x[0] >= cutoff]
        volume = sum(x[2] for x in sample)
        windows[minutes] = (
            100 * sum(x[2] for x in sample if x[1] == "Buy") / volume
            if complete and volume > 0 else None
        )
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
        "windows": windows,
        "trade_age_seconds": max(0, (datetime.now(UTC).timestamp() * 1000 - newest) / 1000),
        "trade_coverage_seconds": (newest - oldest) / 1000,
    }


def score_signals(frames, flow, spread_pct, max_spread_pct):
    """Separate, explainable LONG/SHORT scores; heuristic, not calibrated probabilities."""
    f1, f3, f5, f15 = (frames[x] for x in INTERVALS)
    scores = {}
    for side in ("LONG", "SHORT"):
        sign = 1 if side == "LONG" else -1
        trend = (f15["price"] > f15["ema20"] > f15["ema50"] if sign > 0
                 else f15["price"] < f15["ema20"] < f15["ema50"])
        structure = (f5["price"] > f5["ema20"] if sign > 0
                     else f5["price"] < f5["ema20"])
        breakout = (f5["price"] > f5["high20"] if sign > 0
                    else f5["price"] < f5["low20"])
        momentum = (f3["price"] > f3["ema20"] and f3["rsi"] >= 52 if sign > 0
                    else f3["price"] < f3["ema20"] and f3["rsi"] <= 48)
        volume = f5["volume_ratio"] >= 1.3
        entry = (f1["price"] > f1["ema20"] and
                 f1["last"]["close"] > f1["last"]["open"] if sign > 0 else
                 f1["price"] < f1["ema20"] and
                 f1["last"]["close"] < f1["last"]["open"])
        spread_ok = spread_pct is not None and 0 <= spread_pct <= max_spread_pct
        volatility_ok = 0 < f5["atr"] / f5["price"] <= 0.04
        parts = {
            "15m_trend": 25 if trend else 0,
            "5m_structure": 25 if structure or breakout else 0,
            "3m_momentum": (12 if momentum else 0) + (8 if volume else 0),
            "1m_entry": 15 if entry else 0,
            "risk": (8 if spread_ok else 0) + (7 if volatility_ok else 0),
        }
        score = sum(parts.values())
        # Last minute is most relevant to entry; full recent-trade sample
        # can be less than 2 minutes on liquid markets.
        one_min = flow["windows"].get(1) if flow is not None else None
        recent = (one_min if one_min is not None else
                  flow["buy_pct"] if flow is not None else None)
        opposing = (recent is not None and
                    ((side == "LONG" and recent <= 35) or
                     (side == "SHORT" and recent >= 65)))
        stale = flow is None or flow["trade_age_seconds"] > 10
        # Do not promote a technical signal when fresh taker flow strongly opposes it.
        blocked = opposing or stale or not spread_ok
        reasons = []
        if opposing:
            reasons.append(f"ters agresif islem baskisi (alis %{recent:.1f})")
        if stale:
            reasons.append("canli islem verisi eksik veya eski")
        if not spread_ok:
            reasons.append("spread uygun degil")
        status = ("BEKLE" if blocked or score < 65 else
                  "GUCLU ADAY" if score >= 80 else "IZLEME")
        scores[side] = {"score": score, "parts": parts,
                        "blocked": blocked, "status": status, "reasons": reasons}
    return scores


def adjusted_forecast(prediction, estimate, live_price, frames, flow):
    """Experimental scenario using live order flow; NOT backtested or calibrated."""
    horizon = prediction.horizon_minutes
    baseline_pct = (estimate / float(prediction.current) - 1) * 100
    f1 = frames["1"]
    # Price momentum and flow affect direction, bounded by measured volatility.
    recent_pct = (f1["price"] / f1["previous"]["close"] - 1) * 100
    flow_pct = (flow["buy_pct"] - 50) / 50
    book_pct = (flow["bid_pct"] - 50) / 50
    # Use the shortest complete trade window, if available.
    available = [(minutes, pct) for minutes, pct in flow["windows"].items()
                 if pct is not None]
    if available:
        _, buy_pct = min(available)
        flow_pct = (buy_pct - 50) / 50
    atr_pct = f1["atr"] / f1["price"] * 100
    # Heuristic overlay is intentionally capped; no implied accuracy.
    horizon_scale = min((horizon / 5) ** 0.5, 3.2)
    adjustment = horizon_scale * (0.35 * recent_pct
                                 + 0.45 * atr_pct * flow_pct
                                 + 0.15 * atr_pct * book_pct)
    adjustment = max(-2 * atr_pct * horizon_scale,
                     min(2 * atr_pct * horizon_scale, adjustment))
    projected_pct = baseline_pct + adjustment
    return live_price * (1 + projected_pct / 100), projected_pct


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
    result["live_price"] = float(item["lastPrice"])
    result["spread_pct"] = spread
    return result


async def run(args, db=None):
    now = datetime.now(UTC)
    print(f"BYBIT AI SIGNAL BOT V1 | {now:%Y-%m-%d %H:%M} UTC | SADECE SINYAL")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
        for symbol in SYMBOLS:
            try:
                result = await scan_symbol(session, symbol, now, args)
                print(f"\n{symbol} | {result['direction']} | son mum={result['price']:.8g} "
                      f"| CANLI lastPrice={result['live_price']:.8g}")
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
                    print(f"  Son islem gecikmesi={flow['trade_age_seconds']:.1f}sn "
                          f"| ornek kapsam={flow['trade_coverage_seconds']:.0f}sn")
                    print("  Zaman bazli agresif alis: " + " | ".join(
                        f"{minutes}dk=" + (f"%{pct:.1f}" if pct is not None else "VERI EKSIK")
                        for minutes, pct in flow["windows"].items()))
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError,
                        TypeError, RuntimeError, IndexError, ZeroDivisionError) as exc:
                    flow = None
                    print(f"  Alis/satis verisi alinamadi: {exc}")
                scores = score_signals(result["frames"], flow, result["spread_pct"],
                                       args.max_spread_pct)
                for side, details in scores.items():
                    print(f"  {side} PUAN={details['score']}/100 | {details['status']} "
                          f"| " + ", ".join(
                              f"{key}={value}" for key, value in details["parts"].items()))
                    if details["reasons"]:
                        print("    Engeller: " + "; ".join(details["reasons"]))
                if result["direction"] in ("LONG", "SHORT"):
                    decision = scores[result["direction"]]
                    if decision["status"] != "GUCLU ADAY":
                        print(f"  UYARI: Teknik {result['direction']} sinyali "
                              f"puan/akis filtresini gecmedi; ISLEM ADAYI DEGIL.")
                try:
                    forecasts = await raw_forecasts(session, symbol, now)
                    observed_at = datetime.now(UTC)
                    for forecast, estimate in forecasts:
                        change = (estimate / float(forecast.current) - 1) * 100
                        label = "MODEL BAZDAN IYI" if forecast.beats_naive else "MODEL ZAYIF"
                        tendency = "YUKARI" if change > 0 else "ASAGI" if change < 0 else "YATAY"
                        print(f"  {forecast.horizon_minutes}dk model tahmini={estimate:.8g} USDT "
                              f"| degisim={change:+.3f}% | yon={tendency} | {label} "
                              f"| model hata={float(forecast.model_mae_pct):.3f}% "
                              f"| baz hata={float(forecast.naive_mae_pct):.3f}%")
                        if db is not None:
                            record(db, symbol, observed_at, forecast.horizon_minutes,
                                   "ridge", result["live_price"],
                                   result["live_price"] * (estimate / float(forecast.current)))
                            record(db, symbol, observed_at, forecast.horizon_minutes,
                                   "unchanged", result["live_price"], result["live_price"])
                        if flow is not None:
                            scenario, scenario_pct = adjusted_forecast(
                                forecast, estimate, result["live_price"], result["frames"], flow)
                            if db is not None:
                                record(db, symbol, observed_at, forecast.horizon_minutes,
                                       "flow_heuristic", result["live_price"], scenario)
                            print(f"    Canli akis senaryosu (DOGRULANMAMIS)="
                                  f"{scenario:.8g} USDT | degisim={scenario_pct:+.3f}% "
                                  f"| referans lastPrice={result['live_price']:.8g}")
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
        if db is not None:
            try:
                count = await settle(db, session, get)
                print(f"\nDogrulanan yeni tahmin: {count}")
                print(report(db))
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError,
                    TypeError, RuntimeError, IndexError) as exc:
                print(f"\nTahmin dogrulama hatasi: {exc}")
    print("\nCanli akis senaryosu sezgiseldir, backtest edilmemistir; islem sinyali degildir. Emir gonderilmez.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--equity", type=float, default=1000, help="Example account equity USDT")
    parser.add_argument("--risk-pct", type=float, default=1.0, help="Equity risk per signal (%)")
    parser.add_argument("--cost-pct", type=float, default=0.15,
                        help="Estimated round-trip fees+slippage as % of notional")
    parser.add_argument("--max-spread-pct", type=float, default=0.10)
    parser.add_argument("--watch", action="store_true",
                        help="Her dakika analiz yap, tahminleri kaydet ve dogrula")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--tracker-db", default="data/bybit_predictions.sqlite3")
    args = parser.parse_args()
    if args.equity <= 0 or not 0 < args.risk_pct <= 2 or not 0 <= args.cost_pct <= 2:
        parser.error("Invalid risk parameters")
    if args.interval_seconds < 30:
        parser.error("interval-seconds must be >= 30")
    async def execute():
        db = connect(args.tracker_db)
        try:
            while True:
                await run(args, db)
                if not args.watch:
                    break
                await asyncio.sleep(args.interval_seconds)
        finally:
            db.close()
    try:
        asyncio.run(execute())
    except KeyboardInterrupt:
        print("\nIzleme durduruldu; tahminler veritabaninda saklandi.")


if __name__ == "__main__":
    main()
