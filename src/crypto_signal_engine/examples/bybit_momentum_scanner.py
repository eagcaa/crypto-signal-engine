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
    high20 = max(float(r[2]) for r in candles[-21:-1])
    breakout = prices[-1] > high20
    # Positive momentum + volume + OI confirmation. No claim of probability.
    score = (2 * (ret5 > .15) + 2 * (ret15 > .25)
             + 2 * (volume_ratio >= 1.5) + 2 * breakout
             + 2 * (oi_change_pct is not None and oi_change_pct > .3))
    return {
        "symbol": symbol, "price": prices[-1], "ret5": ret5,
        "ret15": ret15, "volume_ratio": volume_ratio,
        "oi_change_pct": oi_change_pct, "breakout": breakout,
        "score": score, "turnover24h": float(turnover24h),
        "observed_at": datetime.fromtimestamp(
            int(candles[-1][0]) / 1000, UTC).isoformat(),
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
             "breakout": candidate["breakout"]}
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
            output.write(json.dumps(event, ensure_ascii=False) + "\\n")
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
