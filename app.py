import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, jsonify, request

app = Flask(__name__)

AI_UNIVERSE = """AAPL AMD AMAT ANET ASML AVGO CDNS CLS COHR CRDO CRM CSCO DELL EQIX FSLR GEV GLW GOOG GOOGL HPE IBM INTC LITE LRCX META MRVL MSFT MU NBIS NEE NFLX NOW NVDA NXPI ORCL PANW PLTR QCOM ROK ROP SMCI SNPS TSM VRT WDC WDAY ZS""".split()

HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Mosquito V5.8 Master</title><style>
:root{--red:#ed1c24;--blue:#1572e8;--ink:#101827}*{box-sizing:border-box}body{margin:0;background:linear-gradient(160deg,#061329,#112b55);font-family:system-ui,-apple-system,sans-serif;color:white;min-height:100vh}main{max-width:760px;margin:auto;padding:calc(18px + env(safe-area-inset-top)) 16px 40px}header{display:flex;align-items:center;gap:12px}h1{margin:0;letter-spacing:3px}header p{margin:0;color:#9fc7ff;font-weight:800}.bug{height:58px;width:58px;border-radius:50%;background:var(--red);display:grid;place-items:center;font-size:34px;font-weight:900;box-shadow:0 0 0 5px #fff}.ticker{margin:20px 0 0;background:#020b18;border:1px solid #29496f;border-radius:14px;padding:12px}.ticker>div:first-child{display:flex;justify-content:space-between;color:#8fafd3;font-size:11px}#ticker-row{display:flex;gap:18px;overflow-x:auto;padding:10px 0 8px;white-space:nowrap}.ticker small{color:#9bb0c9}.ok{color:#55d986!important}.warn{color:#ffd166!important}.error{color:#ff7b7b!important}.hero{margin:14px 0;background:linear-gradient(135deg,#fff,#dbe8f8);color:var(--ink);padding:22px;border-radius:22px}.hero>span{background:var(--red);color:#fff;font-weight:900;padding:5px 9px;border-radius:7px;font-size:12px}.hero h2{font-size:32px;line-height:1.02;margin:18px 0}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.stats div{background:#fff;padding:10px;border-radius:10px}.stats small{display:block;color:#65738b;font-size:10px}.controls{display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px}button{border:0;border-radius:12px;padding:16px 8px;font-weight:900;color:#fff;background:var(--blue)}.go{background:#13a44b}.stop{background:var(--red)}#message{min-height:24px;color:#cbd9eb}.card{background:#fff;color:var(--ink);border-radius:18px;padding:15px}.cardhead{display:flex;justify-content:space-between;align-items:center}.picks div{display:grid;grid-template-columns:34px 1fr 70px 58px;border-top:1px solid #e7ecf2;padding:10px 4px}.picks span,.picks small{text-align:right}footer{text-align:center;color:#9bb0c9;font-size:11px;padding:20px}@media(max-width:390px){.controls{grid-template-columns:1fr 1fr}.controls .go{grid-column:1/-1}}
</style></head><body><main><header><div class="bug">$</div><div><h1>MOSQUITO</h1><p>V5.8 MASTER</p></div></header><section class="ticker"><div><b>LIVE TICKER</b><span id="ticker-source">CONNECTING</span></div><div id="ticker-row">Loading quotes…</div><small id="ticker-state">Waiting for market data</small></section><section class="hero"><span>PAPER PREVIEW</span><h2>Sucking the profit<br>out of the market.</h2><div class="stats"><div><small>STRATEGY</small><b>Top 50</b></div><div><small>WEIGHT</small><b>Equal</b></div><div><small>UNIVERSE</small><b>AI Only</b></div></div></section><section class="controls"><button class="go" onclick="scan(50)">GO · LOAD 50</button><button onclick="scan(25)">VIEW 25</button><button class="stop" onclick="stopBot()">STOP</button></section><p id="message">Ready to generate the next V5.8 Master watchlist.</p><section class="card"><div class="cardhead"><h3>V5.8 WATCHLIST</h3><span id="gate">—</span></div><div id="picks" class="picks"></div></section><footer>Research and paper preview only · Order execution is disabled</footer></main><script>
async function updateTicker(){const s=document.getElementById('ticker-state');try{const r=await fetch('/api/quotes',{cache:'no-store'}),d=await r.json();if(!r.ok)throw Error(d.error||'Quote request failed');document.getElementById('ticker-source').textContent=d.source==='alpaca'?'ALPACA LIVE':'MARKET DATA';document.getElementById('ticker-row').innerHTML=d.quotes.map(q=>`<span><b>${q.symbol}</b> $${Number(q.price).toFixed(2)}</span>`).join('');s.textContent=`Updated ${new Date(d.generated_at).toLocaleTimeString()}${d.warning?' · Alpaca not connected':''}`;s.className=d.warning?'warn':'ok'}catch(e){s.textContent=`Ticker error · ${e.message}`;s.className='error'}}
async function scan(count){const m=document.getElementById('message');m.textContent='Scanning market data…';document.getElementById('picks').innerHTML='';try{const r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count})}),d=await r.json();if(!r.ok)throw Error(d.error||'Scan failed');m.textContent=`Generated ${d.count} picks · ${new Date(d.generated_at).toLocaleString()}`;document.getElementById('gate').textContent=d.april_trade_allowed?'SIGNAL: GO':'APRIL GATE: STOP';document.getElementById('picks').innerHTML=d.picks.map(x=>`<div><b>${x.rank}</b><strong>${x.ticker}</strong><span>${x.score}</span><small>${x.weight_pct}%</small></div>`).join('')}catch(e){m.textContent=e.message}}function stopBot(){document.getElementById('message').textContent='Stopped. No orders are being submitted.';document.getElementById('gate').textContent='STOPPED'}updateTicker();setInterval(updateTicker,30000);
</script></body></html>'''


def _credentials():
    key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID")
    secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    return key, secret


def _rank(series):
    return series.rank(pct=True).fillna(0.5)


def build_watchlist(count):
    data = yf.download(AI_UNIVERSE, period="5y", interval="1d", auto_adjust=True, progress=False, threads=True)
    if data.empty:
        raise RuntimeError("Market data is temporarily unavailable")
    close, volume = data["Close"], data["Volume"]
    eligible = [ticker for ticker in close.columns if close[ticker].dropna().shape[0] >= 1008]
    if not eligible:
        raise RuntimeError("No stocks met the four-year history requirement")
    close, volume = close[eligible].ffill(), volume[eligible].fillna(0)
    latest = close.iloc[-1]
    frame = pd.DataFrame({
        "m1": latest / close.iloc[-22] - 1, "m3": latest / close.iloc[-66] - 1,
        "m6": latest / close.iloc[-132] - 1, "m12": latest / close.iloc[-252] - 1,
        "breakout": latest / close.tail(252).max() - 1,
        "volume": volume.tail(22).mean() / volume.tail(66).mean() - 1,
        "quality": close.pct_change().tail(252).mean() / close.pct_change().tail(252).std(),
    }).replace([np.inf, -np.inf], np.nan).dropna()
    frame["score"] = (.10*_rank(frame.m1)+.15*_rank(frame.m3)+.25*_rank(frame.m6)+
                      .20*_rank(frame.m12)+.12*_rank(frame.breakout)+.08*_rank(frame.volume)+
                      .10*_rank(frame.quality))
    picks = frame.sort_values("score", ascending=False).head(count)
    rows = [{"rank": n+1, "ticker": ticker, "score": round(float(row.score)*100, 2),
             "weight_pct": round(100/len(picks), 2)} for n, (ticker, row) in enumerate(picks.iterrows())]
    positive = float(frame.m1.median()) > 0
    return {"strategy":"V5.8 Master", "count":len(rows), "picks":rows,
            "positive_signal":positive,
            "april_trade_allowed":datetime.now(timezone.utc).month != 4 or positive,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "notice":"Research and Alpaca paper-preview only. Order execution is disabled."}


@app.get("/")
def dashboard(): return HTML


@app.get("/health")
def health(): return jsonify(status="ok", strategy="V5.8 Master")


@app.get("/api/status")
def status():
    return jsonify(name="Mosquito AI Trading Bot", strategy="V5.8 Master", mode="paper-preview",
                   order_execution=False, generated_at=datetime.now(timezone.utc).isoformat())


@app.get("/api/account")
def account():
    key, secret = _credentials()
    if not key or not secret:
        return jsonify(connected=False, mode="paper", order_execution=False, message="Alpaca paper credentials are not configured")
    try:
        from alpaca.trading.client import TradingClient
        acct = TradingClient(key, secret, paper=True).get_account()
        return jsonify(connected=True, mode="paper", order_execution=False, equity=float(acct.equity), buying_power=float(acct.buying_power))
    except Exception as exc:
        return jsonify(connected=False, mode="paper", order_execution=False, message=str(exc)), 503


@app.get("/api/quotes")
def quotes():
    symbols = [x.strip().upper() for x in request.args.get("symbols", "NVDA,MSFT,AMD,AVGO,PLTR").split(",")]
    symbols = [x for x in symbols if x.isalnum() and len(x) <= 10][:12]
    if not symbols: return jsonify(error="No valid symbols supplied"), 400
    now = datetime.now(timezone.utc).isoformat()
    key, secret = _credentials()
    alpaca_error = "Alpaca credentials are not configured"
    if key and secret:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest
            result = StockHistoricalDataClient(key, secret).get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=symbols, feed=os.getenv("ALPACA_DATA_FEED", "iex")))
            rows = []
            for symbol in symbols:
                quote = result.get(symbol)
                if quote:
                    rows.append({"symbol":symbol, "price":round((float(quote.ask_price)+float(quote.bid_price))/2, 4), "timestamp":quote.timestamp.isoformat()})
            if rows: return jsonify(source="alpaca", generated_at=now, quotes=rows)
            alpaca_error = "Alpaca returned no quotes"
        except Exception as exc: alpaca_error = str(exc)
    try:
        rows = []
        for symbol in symbols:
            price = yf.Ticker(symbol).fast_info.get("last_price")
            if price is not None: rows.append({"symbol":symbol, "price":round(float(price), 4), "timestamp":now})
        if not rows: raise RuntimeError("Market-data fallback returned no quotes")
        return jsonify(source="market-data-fallback", generated_at=now, quotes=rows, warning=alpaca_error)
    except Exception as exc:
        return jsonify(error=str(exc), upstream_error=alpaca_error, generated_at=now), 503


@app.post("/api/scan")
def scan():
    try:
        count = max(25, min(int((request.get_json(silent=True) or {}).get("count", 50)), 50))
        return jsonify(build_watchlist(count))
    except (TypeError, ValueError): return jsonify(error="Count must be a number from 25 through 50"), 400
    except Exception as exc: return jsonify(error=str(exc)), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
