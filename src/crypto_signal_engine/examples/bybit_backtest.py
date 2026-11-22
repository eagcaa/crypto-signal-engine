"""Research-only candle backtest; no order submission or fabricated historical prices.

CSV schema: symbol,interval,open_time,open,high,low,close,volume
Timestamps must be UTC ISO8601; rows are candle OPEN times. All candles must be closed.
"""
import argparse
import csv
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from crypto_signal_engine.domain.candles import Candle
from crypto_signal_engine.signals.bybit_v1 import evaluate

MINUTES = {"1m": 1, "5m": 5, "15m": 15}
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def load_csv(path: Path) -> dict[tuple[str, str], list[Candle]]:
    grouped = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            symbol, interval = row["symbol"].upper(), row["interval"]
            if symbol not in SYMBOLS or interval not in MINUTES:
                raise ValueError(f"Unsupported symbol/interval: {symbol}/{interval}")
            opened = datetime.fromisoformat(row["open_time"].replace("Z", "+00:00"))
            if opened.tzinfo is None:
                raise ValueError("Candle times must include UTC timezone")
            opened = opened.astimezone(UTC)
            minutes = MINUTES[interval]
            candle = Candle(
                symbol=symbol, interval=interval, open_time=opened,
                close_time=opened + timedelta(minutes=minutes),
                open=Decimal(row["open"]), high=Decimal(row["high"]),
                low=Decimal(row["low"]), close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
            )
            if not (candle.low <= min(candle.open, candle.close)
                    <= max(candle.open, candle.close) <= candle.high):
                raise ValueError(f"Invalid OHLC: {symbol} {opened}")
            grouped[(symbol, interval)].append(candle)
    for key, series in grouped.items():
        series.sort(key=lambda c: c.open_time)
        if len({c.open_time for c in series}) != len(series):
            raise ValueError(f"Duplicate candle timestamps: {key}")
    return grouped


def simulate(grouped, symbol: str, *, fee_pct_per_side=Decimal("0.055"),
             slippage_pct_per_side=Decimal("0.02"), initial_equity=Decimal("1000")):
    """One position at a time; enter next 1m OPEN, never on signal close.

    Exits use 1m highs/lows. If both stop and target are hit in the same candle,
    assume stop first. Signals with insufficient history are skipped.
    """
    one = grouped.get((symbol, "1m"), [])
    five = grouped.get((symbol, "5m"), [])
    fifteen = grouped.get((symbol, "15m"), [])
    by_1m_open = {c.open_time: c for c in one}
    equity = initial_equity
    peak = equity
    max_dd = Decimal("0")
    wins = 0
    losses = 0
    trades = []
    busy_until = None
    costs = (fee_pct_per_side + slippage_pct_per_side) / Decimal("100")
    for index in range(89, len(fifteen)):
        anchor = fifteen[index].close_time
        if busy_until is not None and anchor < busy_until:
            continue
        c1 = [c for c in one if c.close_time <= anchor][-120:]
        c5 = [c for c in five if c.close_time <= anchor][-120:]
        c15 = fifteen[max(0, index - 119):index + 1]
        if not (c1 and c5 and c15 and
                c1[-1].close_time == c5[-1].close_time == anchor):
            continue
        decisions = evaluate(symbol, c1, c5, c15, equity=equity,
                             fee_pct_per_side=fee_pct_per_side,
                             slippage_pct_per_side=slippage_pct_per_side)
        candidates = [s for s in decisions if s.side in ("LONG", "SHORT")]
        if not candidates:
            continue
        signal = candidates[0]  # trend priority; no simultaneous positions
        entry_bar = by_1m_open.get(anchor)
        if entry_bar is None:
            continue
        entry = entry_bar.open * (1 + costs * (1 if signal.side == "LONG" else -1)
                                  * (slippage_pct_per_side / (fee_pct_per_side + slippage_pct_per_side))
                                  if (fee_pct_per_side + slippage_pct_per_side) else 1)
        # Re-anchor stop/target distances to the actual next-bar fill.
        stop_distance = abs(signal.entry - signal.stop)
        target_distance = abs(signal.take_profit - signal.entry)
        stop = entry - stop_distance if signal.side == "LONG" else entry + stop_distance
        target = entry + target_distance if signal.side == "LONG" else entry - target_distance
        if entry <= 0 or stop <= 0 or target <= 0:
            continue
        notional = min(signal.notional, equity * Decimal("0.5"))
        quantity = notional / entry
        for bar in one:
            if bar.open_time < anchor:
                continue
            hit_stop = bar.low <= stop if signal.side == "LONG" else bar.high >= stop
            hit_target = bar.high >= target if signal.side == "LONG" else bar.low <= target
            if not hit_stop and not hit_target:
                continue
            # Conservative same-bar fill: stop wins; account for gaps against us.
            exit_price = stop if hit_stop else target
            if hit_stop:
                exit_price = min(stop, bar.open) if signal.side == "LONG" else max(stop, bar.open)
            slip = slippage_pct_per_side / Decimal("100")
            exit_price *= 1 - slip if signal.side == "LONG" else 1 + slip
            gross = (exit_price - entry) * quantity * (1 if signal.side == "LONG" else -1)
            fees = (entry + exit_price) * quantity * fee_pct_per_side / Decimal("100")
            pnl = gross - fees
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100)
            wins += pnl > 0
            losses += pnl <= 0
            busy_until = bar.close_time
            trades.append((signal.strategy, signal.side, anchor, pnl))
            break
        else:
            # No closed exit: do not invent realized PnL.
            break
    return {"symbol": symbol, "trades": len(trades), "wins": wins, "losses": losses,
            "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else None,
            "realized_pnl": round(equity - initial_equity, 4),
            "ending_equity": round(equity, 4), "max_drawdown_pct": round(max_dd, 2),
            "open_or_unresolved_excluded": True}


def main():
    parser = argparse.ArgumentParser(description="Bybit perpetual CSV research backtest")
    parser.add_argument("--csv", type=Path, required=True,
                        help="Real historical linear-perpetual 1m/5m/15m candles CSV")
    args = parser.parse_args()
    grouped = load_csv(args.csv)
    for symbol in SYMBOLS:
        if not all((symbol, tf) in grouped for tf in MINUTES):
            print(f"{symbol}: SKIP missing timeframe(s)")
            continue
        print(simulate(grouped, symbol))


if __name__ == "__main__":
    main()
