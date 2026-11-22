"""Read-only Bybit USDT perpetual momentum scanner; no trading orders.

Run: PYTHONPATH=src python -m crypto_signal_engine.examples.bybit_momentum_scanner
Telegram: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (optional)
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.signals.historical_price_forecast import predict_prices

import aiohttp

BASE = "https://api.bybit.com"
D = Decimal


async def get(session, endpoint, params):
    for attempt in range(3):
        try:
            async with session.get(BASE + endpoint, params=params) as response:
                payload = await response.json(content_type=None)
                if response.status != 200 or payload.get("retCode") != 0:
                    raise RuntimeError(f"Bybit {endpoint}: HTTP {response.status}: {payload.get('retMsg')}")
                return payload["result"]
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)


def select_symbols(instruments, tickers, min_turnover, max_symbols):
    eligible = {
        row["symbol"] for row in instruments
        if row.get("status") == "Trading"
        and row.get("quoteCoin") == "USDT"
        and row.get("contractType") == "LinearPerpetual"
    }
    ranked = sorted(
        (t for t in tickers if t.get("symbol") in eligible
         and D(str(t.get("turnover24h") or "0")) >= D(str(min_turnover))),
        key=lambda t: D(str(t["turnover24h"])), reverse=True)
    selected = ranked[:max_symbols]
    # Always include ETC when it meets the same liquidity and listing filters.
    etc = next((t for t in ranked if t["symbol"] == "ETCUSDT"), None)
    if etc and all(t["symbol"] != "ETCUSDT" for t in selected):
        selected = selected[:-1] + [etc]
    return selected


def score_candidate(symbol, rows, oi_change_pct, turnover24h, now):
    if len(rows) < 31:
        return None
    # Bybit returns reverse-chronological candles; exclude unclosed current minute.
    candles = sorted(rows, key=lambda row: int(row[0]))
    closed_cutoff = int(now.replace(second=0, microsecond=0).timestamp() * 1000)
    candles = [r for r in candles if int(r[0]) < closed_cutoff]
    if len(candles) < 31:
        return None
    candles = candles[-31:]
    if any(int(b[0]) - int(a[0]) != 60000 for a, b in zip(candles, candles[1:])):
        return None
    prices = [float(r[4]) for r in candles]
    volumes = [float(r[6]) for r in candles]  # Bybit kline turnover (USDT)
    if min(prices) <= 0:
        return None
    ret5 = (prices[-1] / prices[-6] - 1) * 100
    ret15 = (prices[-1] / prices[-16] - 1) * 100
    previous_avg = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / previous_avg if previous_avg > 0 else 0
    highs = [float(r[2]) for r in candles]
    lows = [float(r[3]) for r in candles]
    high20 = max(highs[-21:-1])
    ranges = [max(highs[i] - lows[i], abs(highs[i] - prices[i-1]),
                  abs(lows[i] - prices[i-1])) for i in range(1, len(prices))]
    atr_pct = sum(ranges[-14:]) / 14 / prices[-1] * 100
    old_atr = sum(ranges[-28:-14]) / 14 / prices[-15] * 100
    volatility_expansion = atr_pct / old_atr if old_atr > 0 else None
    changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gain = sum(max(x, 0) for x in changes[-14:])
    loss = sum(max(-x, 0) for x in changes[-14:])
    rsi = 100 * gain / (gain + loss) if gain + loss else 50
    candle_range = highs[-1] - lows[-1]
    close_location = ((prices[-1] - lows[-1]) / candle_range
                      if candle_range else 0.5)
    breakout = prices[-1] > high20
    pattern = triangle_pattern(candles)
    # Positive momentum + volume + OI confirmation. No claim of probability.
    score = (2 * (ret5 > .15) + 2 * (ret15 > .25)
             + 2 * (volume_ratio >= 1.5) + 2 * breakout
             + 2 * (oi_change_pct is not None and oi_change_pct > .3))
    return {
        "symbol": symbol, "price": prices[-1], "ret5": ret5,
        "ret15": ret15, "volume_ratio": volume_ratio,
        "oi_change_pct": oi_change_pct, "breakout": breakout,
        "score": score, "turnover24h": float(turnover24h),
        "atr_pct": atr_pct, "volatility_expansion": volatility_expansion,
        "rsi": rsi, "close_location": close_location,
        "pattern": pattern,
        "observed_at": datetime.fromtimestamp(
            int(candles[-1][0]) / 1000, UTC).isoformat(),
    }


def triangle_pattern(candles):
    """Heuristic converging swing boundaries on 30 closed 1m candles.

    Returns descriptive evidence, not a chart drawing or trading signal.
    """
    if len(candles) < 30:
        return None
    rows = candles[-30:]
    highs = [float(x[2]) for x in rows]
    lows = [float(x[3]) for x in rows]
    closes = [float(x[4]) for x in rows]
    if min(lows) <= 0:
        return None

    # Compare high/low envelopes of two non-overlapping 10m blocks.
    early_high, late_high = max(highs[:10]), max(highs[10:20])
    early_low, late_low = min(lows[:10]), min(lows[10:20])
    early_width = early_high - early_low
    late_width = late_high - late_low
    if early_width <= 0:
        return None
    narrowing = late_width / early_width
    descending_highs = late_high < early_high
    ascending_lows = late_low > early_low
    converging = descending_highs and ascending_lows and narrowing < 0.85
    # Breakout requires the most recent closed candle above pre-breakout
    # resistance, with its close beyond the prior 20m upper envelope.
    resistance = max(highs[-21:-1])
    support = min(lows[-21:-1])
    breakout = closes[-1] > resistance
    return {
        "triangle_converging": converging,
        "triangle_width_ratio": round(narrowing, 3),
        "triangle_resistance": resistance,
        "triangle_support": support,
        "triangle_breakout": bool(converging and breakout),
        "triangle_status": ("KIRILIM" if converging and breakout
                            else "SIKISMA" if converging else "YOK"),
    }


async def get_instruments(session):
    items, cursor = [], None
    while True:
        params = {"category": "linear", "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        result = await get(session, "/v5/market/instruments-info", params)
        items.extend(result.get("list", []))
        cursor = result.get("nextPageCursor")
        if not cursor:
            return items


async def scan_symbol(session, semaphore, ticker, now):
    symbol = ticker["symbol"]
    async with semaphore:
        try:
            klines = await get(session, "/v5/market/kline", {
                "category": "linear", "symbol": symbol, "interval": "1",
                "limit": "40"})
            oi = await get(session, "/v5/market/open-interest", {
                "category": "linear", "symbol": symbol,
                "intervalTime": "5min", "limit": "3"})
            oi_rows = sorted(oi.get("list", []), key=lambda x: int(x["timestamp"]))
            oi_change = None
            if len(oi_rows) >= 2:
                old, new = float(oi_rows[-2]["openInterest"]), float(oi_rows[-1]["openInterest"])
                if old > 0:
                    oi_change = (new / old - 1) * 100
            return score_candidate(symbol, klines.get("list", []), oi_change,
                                   ticker["turnover24h"], now)
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            print(f"SKIP {symbol}: {exc}")
            return None

async def price_targets(session, symbol, now):
    """Return model-gated 5m and 15m USDT point targets for a ranked coin."""
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
                open=D(row[1]), high=D(row[2]), low=D(row[3]),
                close=D(row[4]), volume=D(row[5]), quote_volume=D(row[6])))
    candles = sorted({c.open_time: c for c in candles}.values(), key=lambda c: c.open_time)
    return predict_prices(candles)


async def trade_pressure(session, symbol):
    """Aggressive buy share among latest 500 trades; not complete order flow."""
    result = await get(session, "/v5/market/recent-trade", {
        "category": "linear", "symbol": symbol, "limit": "500"})
    trades = result.get("list", [])
    buy = sum(float(t["size"]) * float(t["price"]) for t in trades
              if t.get("side") == "Buy")
    sell = sum(float(t["size"]) * float(t["price"]) for t in trades
               if t.get("side") == "Sell")
    return (buy / (buy + sell) * 100 if buy + sell else None, len(trades))


async def send_telegram(session, message):
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    async with session.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message}) as response:
        payload = await response.json(content_type=None)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram HTTP {response.status}: {payload.get('description')}")
    print("Telegram: message delivered successfully")


async def run(args):
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        instruments = await get_instruments(session)
        ticker_data = await get(session, "/v5/market/tickers", {"category": "linear"})
        tickers = select_symbols(instruments, ticker_data["list"],
                                 args.min_turnover, args.max_symbols)
        print(f"Scanning {len(tickers)} liquid USDT perpetual symbols...")
        now = datetime.now(UTC)
        semaphore = asyncio.Semaphore(6)
        candidates = await asyncio.gather(*[
            scan_symbol(session, semaphore, ticker, now) for ticker in tickers])
        ranked = sorted((c for c in candidates if c is not None),
                        key=lambda c: (c["score"], c["ret5"]), reverse=True)
        # Discovery and forecasting are separate: a weak regression model
        # must not hide an early-moving coin from the watchlist.
        watchlist = ranked[:args.top]
        lines = [f"BYBIT ERKEN HAREKET RADARI | {now:%Y-%m-%d %H:%M} UTC"]
        if not watchlist:
            lines.append("Yeterli kapali mum verisi bulunan coin yok.")
        alerts = []
        for i, candidate in enumerate(watchlist, 1):
            oi = candidate["oi_change_pct"]
            oi_text = f"{oi:+.2f}%" if oi is not None else "N/A"
            reasons = []
            if candidate["volume_ratio"] >= 1.5:
                reasons.append("HACIM_ARTISI")
            if oi is not None and oi > 0.3:
                reasons.append("OI_ARTISI")
            if candidate["breakout"]:
                reasons.append("TEPE_KIRILIMI")
            if candidate["ret5"] > 0.15:
                reasons.append("5DK_IVME")
            if candidate["ret15"] > 0.25:
                reasons.append("15DK_IVME")
            lines.append(
                f"{i}. {candidate['symbol']} | fiyat={candidate['price']:.8g} USDT "
                f"| radar={candidate['score']}/10 | 5dk={candidate['ret5']:+.2f}% "
                f"| 15dk={candidate['ret15']:+.2f}% "
                f"| hacim={candidate['volume_ratio']:.2f}x "
                f"| OI5dk={oi_text} | kirilim={'EVET' if candidate['breakout'] else 'HAYIR'}")
            lines.append(f"   Gerekce: {', '.join(reasons) if reasons else 'ZAYIF'}")
            expansion = candidate["volatility_expansion"]
            expansion_text = f"{expansion:.2f}x" if expansion is not None else "N/A"
            lines.append(
                f"   Grafik: ATR14={candidate['atr_pct']:.3f}% "
                f"| volatilite_genisleme={expansion_text} "
                f"| RSI14={candidate['rsi']:.1f} "
                f"| mum_kapanis_konumu={candidate['close_location']:.0%}")
            pattern = candidate["pattern"]
            if pattern:
                lines.append(
                    f"   Formasyon: {pattern['triangle_status']} "
                    f"| direnc={pattern['triangle_resistance']:.8g} "
                    f"| destek={pattern['triangle_support']:.8g} "
                    f"| bant_orani={pattern['triangle_width_ratio']:.2f}x")
            try:
                share, count = await trade_pressure(session, candidate["symbol"])
                share_text = f"{share:.1f}%" if share is not None else "N/A"
                lines.append(f"   Agresif_alis_payi={share_text} | islem_ornegi={count}")
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                lines.append(f"   Agresif_alis_payi=veri_yok ({exc})")
            try:
                predictions = await price_targets(session, candidate["symbol"], now)
                for prediction in predictions:
                    source = "MODEL" if prediction.beats_naive else "BASELINE_NO_EDGE"
                    lines.append(
                        f"   +{prediction.horizon_minutes}dk tahmini="
                        f"{prediction.estimate:.8f} USDT "
                        f"({prediction.change_pct:+.3f}%) [{source}]")
            except (ValueError, RuntimeError, KeyError, ZeroDivisionError) as exc:
                lines.append(f"   Fiyat tahmini hesaplanamadi: {exc}")
            # Alert on radar evidence, not on a model's unproven directional gate.
            if candidate["score"] >= args.min_score:
                alerts.append(candidate["symbol"])
        lines.append("Radar siralamasi erken hareket arastirmasidir; yukselis garantisi veya alim emri degildir.")
        message = "\n".join(lines)
        print(message)
        if args.telegram and alerts:
            await send_telegram(session, message)
        return {"at": now.isoformat(), "candidates": [
            {"symbol": candidate["symbol"], "price": candidate["price"],
             "score": candidate["score"], "ret5": candidate["ret5"],
             "ret15": candidate["ret15"], "volume_ratio": candidate["volume_ratio"],
             "oi_change_pct": candidate["oi_change_pct"],
             "breakout": candidate["breakout"],
             "atr_pct": candidate["atr_pct"],
             "volatility_expansion": candidate["volatility_expansion"],
             "rsi": candidate["rsi"],
             "close_location": candidate["close_location"],
             "pattern": candidate["pattern"]}
            for candidate in ranked]}


def record_watch_cycle(path, snapshot):
    """Append observations and settle previous 5m/15m outcomes.

    Each outcome uses the first subsequent scan at/after its horizon,
    so evaluation delay is recorded and never mistaken for exact timing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending_path = path.with_name(path.stem + "_pending.json")
    pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else []
    now = datetime.fromisoformat(snapshot["at"])
    prices = {c["symbol"]: c["price"] for c in snapshot["candidates"]}
    events = []
    remaining = []
    for entry in pending:
        due = datetime.fromisoformat(entry["due"])
        if now < due or entry["symbol"] not in prices:
            remaining.append(entry)
            continue
        actual = prices[entry["symbol"]]
        events.append({
            "type": "outcome", "at": snapshot["at"],
            "signal_at": entry["signal_at"], "symbol": entry["symbol"],
            "horizon_minutes": entry["horizon_minutes"],
            "start_price": entry["start_price"], "actual_price": actual,
            "realized_pct": round((actual / entry["start_price"] - 1) * 100, 5),
            "evaluation_delay_seconds": round((now - due).total_seconds()),
            "score": entry["score"]})
    for candidate in snapshot["candidates"]:
        # Record all observed coins; no price-model filter.
        events.append({"type": "observation", "at": snapshot["at"], **candidate})
        for horizon in (5, 15):
            pending_key = (snapshot["at"], candidate["symbol"], horizon)
            if not any((p["signal_at"], p["symbol"], p["horizon_minutes"]) == pending_key
                       for p in remaining):
                remaining.append({
                    "signal_at": snapshot["at"], "symbol": candidate["symbol"],
                    "horizon_minutes": horizon, "score": candidate["score"],
                    "start_price": candidate["price"],
                    "due": (now + timedelta(minutes=horizon)).isoformat()})
    with path.open("a", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")
    # Replace pending atomically to avoid truncation on interrupted writes.
    temp = pending_path.with_suffix(".tmp")
    temp.write_text(json.dumps(remaining, ensure_ascii=False), encoding="utf-8")
    temp.replace(pending_path)
    outcomes = [e for e in events if e["type"] == "outcome"]
    print(f"Kayit: {path} | gozlem={len(snapshot['candidates'])} | "
          f"olculen_sonuc={len(outcomes)} | bekleyen={len(remaining)}")


async def watch(args):
    while True:
        try:
            snapshot = await run(args)
            record_watch_cycle(args.history, snapshot)
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError,
                OSError, KeyError) as exc:
            print(f"Tarama hatasi (sonraki dongude yeniden denenecek): {exc}")
        if not args.watch:
            break
        await asyncio.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-turnover", type=float, default=5_000_000)
    parser.add_argument("--max-symbols", type=int, default=150)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--min-score", type=int, default=6)
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--watch", action="store_true", help="Repeat scan and record outcomes")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between scans")
    parser.add_argument("--history", default="data/bybit_radar_history.jsonl")
    args = parser.parse_args()
    if not (1 <= args.max_symbols <= 500 and 1 <= args.top <= 50
            and 0 <= args.min_score <= 10 and args.min_turnover >= 0
            and args.interval >= 30):
        parser.error("Invalid scanner parameters")
    try:
        asyncio.run(watch(args))
    except KeyboardInterrupt:
        print("Radar durduruldu.")


if __name__ == "__main__":
    main()
