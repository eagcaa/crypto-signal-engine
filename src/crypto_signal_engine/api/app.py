from decimal import Decimal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from crypto_signal_engine import __version__
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    ResearchFeatureRepository,
    create_database_engine,
    create_session_factory,
)
from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.market.state_alerts import MarketStateAlertMonitor


app = FastAPI(
    title="Crypto Signal Engine",
    version=__version__,
)


def _average_optional(
    *values: Decimal | None,
) -> Decimal | None:
    available = [value for value in values if value is not None]
    if not available:
        return None
    return sum(available, Decimal("0")) / Decimal(len(available))


def market_state_payload(
    snapshot: ResearchFeatureSnapshot,
) -> dict[str, object]:
    alerts = MarketStateAlertMonitor().evaluate(snapshot)

    return {
        "symbol": snapshot.symbol,
        "timestamp": snapshot.timestamp,
        "price": snapshot.price,
        "data_quality": snapshot.market_data_quality,
        "history_seconds": snapshot.history_seconds,
        "alerts": [
            {
                "key": alert.key,
                "title": alert.title,
                "summary": alert.summary,
                "details": alert.details,
            }
            for alert in alerts
        ],
        "positioning": {
            "oi_change_5m_avg_pct": _average_optional(
                snapshot.binance_oi_change_5m_pct,
                snapshot.bybit_oi_change_5m_pct,
            ),
            "binance_oi_change_5m_pct": (
                snapshot.binance_oi_change_5m_pct
            ),
            "bybit_oi_change_5m_pct": snapshot.bybit_oi_change_5m_pct,
            "binance_funding_rate": snapshot.binance_funding_rate,
            "bybit_funding_rate": snapshot.bybit_funding_rate,
            "binance_long_short_ratio": snapshot.binance_long_short_ratio,
            "bybit_long_short_ratio": snapshot.bybit_long_short_ratio,
            "binance_top_trader_ratio": (
                snapshot.binance_top_trader_long_short_ratio
            ),
            "binance_taker_buy_sell_ratio": (
                snapshot.binance_taker_buy_sell_ratio
            ),
        },
        "flow": {
            "spot_cvd_ratio_1m": snapshot.spot_cvd_ratio_1m,
            "spot_cvd_ratio_5m": snapshot.spot_cvd_ratio_5m,
            "spot_cvd_ratio_15m": snapshot.spot_cvd_ratio_15m,
            "futures_cvd_ratio_1m": snapshot.futures_cvd_ratio_1m,
            "futures_cvd_ratio_5m": snapshot.futures_cvd_ratio_5m,
            "futures_cvd_ratio_15m": snapshot.futures_cvd_ratio_15m,
            "spot_minus_futures_5m": (
                snapshot.spot_cvd_ratio_5m
                - snapshot.futures_cvd_ratio_5m
            ),
        },
        "trend": {
            "score_5m": snapshot.trend_score_5m,
            "score_15m": snapshot.trend_score_15m,
            "score_1h": snapshot.trend_score_1h,
            "score_4h": snapshot.trend_score_4h,
            "regime_5m": snapshot.trend_regime_5m,
            "regime_15m": snapshot.trend_regime_15m,
        },
        "liquidations": {
            "long_5m_usd": snapshot.long_liquidations_5m_usd,
            "short_5m_usd": snapshot.short_liquidations_5m_usd,
            "imbalance_5m": snapshot.liquidation_imbalance_5m,
            "long_15m_usd": snapshot.long_liquidations_15m_usd,
            "short_15m_usd": snapshot.short_liquidations_15m_usd,
            "imbalance_15m": snapshot.liquidation_imbalance_15m,
        },
        "order_book": {
            "binance_imbalance": snapshot.binance_book_imbalance,
            "bybit_imbalance": snapshot.bybit_book_imbalance,
        },
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": __version__,
    }


@app.get("/market-state/{symbol}")
async def market_state(symbol: str) -> dict[str, object]:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    try:
        repository = ResearchFeatureRepository(
            create_session_factory(engine)
        )
        snapshot = await repository.latest(symbol)
    finally:
        await engine.dispose()

    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"No live market state for {symbol.upper()}",
        )

    return market_state_payload(snapshot)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    return """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crypto Market State</title>
<style>
:root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
body { margin: 0; background: #0b0d10; color: #eef2f7; }
main { max-width: 1180px; margin: 0 auto; padding: 24px; }
header { display:flex; justify-content:space-between; gap:16px; align-items:end; }
h1 { margin: 0; font-size: 28px; }
.muted { color:#8d99a8; font-size:13px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
        gap:12px; margin-top:18px; }
.card { background:#141820; border:1px solid #252b36; border-radius:14px;
        padding:16px; }
.card h2 { font-size:14px; color:#aab5c4; margin:0 0 12px; }
.value { font-size:24px; font-weight:700; }
.row { display:flex; justify-content:space-between; gap:10px;
       padding:6px 0; border-bottom:1px solid #202631; }
.row:last-child { border:0; }
.alert { border-left:4px solid #d9a441; margin-top:10px; }
.alert strong { display:block; margin-bottom:6px; }
.ok { color:#6edb98; } .warn { color:#f0c36a; }
#alerts:empty::after { content:"Aktif piyasa olayı yok"; color:#8d99a8; }
</style>
</head>
<body>
<main>
<header>
<div><h1>BTCUSDT Market State</h1>
<div class="muted">Tahmin değil; canlı piyasa durum özeti</div></div>
<div id="updated" class="muted">yükleniyor...</div>
</header>
<div class="grid">
<section class="card"><h2>Fiyat / Veri</h2><div id="price" class="value">-</div>
<div id="quality"></div></section>
<section class="card"><h2>Pozisyonlanma</h2><div id="positioning"></div></section>
<section class="card"><h2>Spot vs Futures</h2><div id="flow"></div></section>
<section class="card"><h2>Likidasyon</h2><div id="liquidations"></div></section>
<section class="card"><h2>Order Book</h2><div id="book"></div></section>
</div>
<section class="card" style="margin-top:12px"><h2>Aktif Uyarılar</h2>
<div id="alerts"></div></section>
</main>
<script>
function fmt(v, digits) {
  if (v === null || v === undefined) return "n/a";
  return Number(v).toFixed(digits === undefined ? 3 : digits);
}
function money(v) {
  if (v === null || v === undefined) return "n/a";
  return "$" + Number(v).toLocaleString(undefined,{maximumFractionDigits:0});
}
function rows(items) {
  return items.map(function(x) {
    return '<div class="row"><span>' + x[0] + '</span><strong>' +
      x[1] + '</strong></div>';
  }).join("");
}
async function refresh() {
  try {
    const r = await fetch("/market-state/BTCUSDT", {cache:"no-store"});
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    document.getElementById("price").textContent = money(d.price);
    document.getElementById("updated").textContent =
      new Date(d.timestamp).toLocaleString();
    document.getElementById("quality").innerHTML = rows([
      ["Data quality", fmt(d.data_quality,2)],
      ["History", d.history_seconds + " s"]
    ]);
    document.getElementById("positioning").innerHTML = rows([
      ["OI 5m avg", fmt(d.positioning.oi_change_5m_avg_pct) + "%"],
      ["Funding Binance", fmt(d.positioning.binance_funding_rate,6)],
      ["Funding Bybit", fmt(d.positioning.bybit_funding_rate,6)],
      ["Long/Short Binance", fmt(d.positioning.binance_long_short_ratio)]
    ]);
    document.getElementById("flow").innerHTML = rows([
      ["Spot CVD 5m", fmt(d.flow.spot_cvd_ratio_5m)],
      ["Futures CVD 5m", fmt(d.flow.futures_cvd_ratio_5m)],
      ["Spot - Futures", fmt(d.flow.spot_minus_futures_5m)],
      ["Trend 5m", fmt(d.trend.score_5m)]
    ]);
    document.getElementById("liquidations").innerHTML = rows([
      ["Long liq 5m", money(d.liquidations.long_5m_usd)],
      ["Short liq 5m", money(d.liquidations.short_5m_usd)],
      ["Imbalance", fmt(d.liquidations.imbalance_5m)]
    ]);
    document.getElementById("book").innerHTML = rows([
      ["Binance", fmt(d.order_book.binance_imbalance)],
      ["Bybit", fmt(d.order_book.bybit_imbalance)]
    ]);
    document.getElementById("alerts").innerHTML = d.alerts.map(function(a) {
      return '<div class="card alert"><strong>' + a.title + '</strong>' +
        '<div>' + a.summary + '</div><div class="muted">' +
        a.details.join(" · ") + '</div></div>';
    }).join("");
  } catch (e) {
    document.getElementById("updated").textContent = "Hata: " + e.message;
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""
