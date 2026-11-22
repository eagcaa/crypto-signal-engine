"""Read-only Bybit USDT perpetual momentum scanner; no trading orders.

Run: PYTHONPATH=src python -m crypto_signal_engine.examples.bybit_momentum_scanner
Telegram: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (optional)
"""
import argparse
import asyncio
import os
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
    # Do not label a sharp short-term reversal as a bullish candidate.\n    if ret5 <= 0 or ret15 <= 0:\n        return None\n    return {\n        "symbol": symbol, "price": prices[-1], "ret5": ret5,
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
        selected = [c for c in ranked if c["score"] >= args.min_score][:args.top]
        lines = [f"BYBIT YUKSELIS ADAYLARI | {now:%Y-%m-%d %H:%M} UTC"]
        if not selected:
            lines.append("Esik ustunde aday yok; sinyal uretilemiyor.")
        for i, c in enumerate(selected, 1):
            oi_text = (f"{c['oi_change_pct']:+.2f}%" if c["oi_change_pct"] is not None
                       else "N/A")
            lines.append(
                f"{i}. {c['symbol']} | score={c['score']}/10 | price={c['price']:.6g} "
                f"| 5m={c['ret5']:+.2f}% 15m={c['ret15']:+.2f}% "
                f"| volume={c['volume_ratio']:.2f}x | OI5m={oi_text} "
                f"| breakout={'YES' if c['breakout'] else 'NO'}")
        for candidate in selected:
            try:
                predictions = await price_targets(session, candidate["symbol"], now)
                for prediction in predictions:
                    source = "MODEL" if prediction.beats_naive else "BASELINE_NO_EDGE"
                    lines.append(
                        f"  {candidate['symbol']} +{prediction.horizon_minutes}m "
                        f"hedef={prediction.estimate:.6f} USDT "
                        f"({prediction.change_pct:+.3f}%) source={source} "
                        f"model_MAE={prediction.model_mae_pct:.4f}% "
                        f"naive_MAE={prediction.naive_mae_pct:.4f}%")
            except (ValueError, RuntimeError, KeyError) as exc:
                lines.append(f"  {candidate['symbol']} hedef hesaplanamadi: {exc}")
        lines.append("Hedefler tahmindir; garanti veya alim emri degildir.")
        message = "\n".join(lines)
        print(message)
        if args.telegram and selected:
            await send_telegram(session, message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-turnover", type=float, default=5_000_000)
    parser.add_argument("--max-symbols", type=int, default=150)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--min-score", type=int, default=6)
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()
    if not (1 <= args.max_symbols <= 500 and 1 <= args.top <= 50
            and 0 <= args.min_score <= 10 and args.min_turnover >= 0):
        parser.error("Invalid scanner parameters")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
