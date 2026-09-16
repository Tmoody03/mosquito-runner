import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, jsonify, request

app = Flask(__name__)

AI_UNIVERSE = """AAPL AMD AMAT ANET ASML AVGO CDNS CLS COHR CRDO CRM CSCO DELL EQIX FSLR GEV GLW GOOG GOOGL HPE IBM INTC LITE LRCX META MRVL MSFT MU NBIS NEE NFLX NOW NVDA NXPI ORCL PANW PLTR QCOM ROK ROP SMCI SNPS TSM VRT WDC WDAY ZS""".split()

HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#020305">
<title>Mosquito V5.8 Master</title>
<style>
:root{--red:#ff253a;--green:#29e27d;--blue:#4ca7ff;--gold:#ffc743;--panel:#0b0e12;--line:#262c35;--muted:#8d98a8}
*{box-sizing:border-box}html{background:#020305}body{margin:0;min-height:100vh;background:radial-gradient(circle at 50% 18%,#161b22 0,#080a0d 28%,#020305 68%);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#fff}
main{width:min(100%,760px);margin:auto;padding:calc(14px + env(safe-area-inset-top)) 14px calc(34px + env(safe-area-inset-bottom))}
.top{display:flex;align-items:center;justify-content:space-between}.brand{display:flex;align-items:center;gap:10px}.mini{width:42px;height:42px;border:1px solid #343b46;border-radius:50%;display:grid;place-items:center;background:#090c10;color:var(--red);font-size:23px;font-weight:1000;box-shadow:0 0 20px #ff253a25}.brand h1{font-size:20px;letter-spacing:3px;margin:0}.brand p{font-size:10px;color:#a8b0bc;letter-spacing:2px;margin:2px 0 0}.status{display:flex;align-items:center;gap:7px;color:#aeb7c5;font-size:10px;font-weight:800;letter-spacing:1px}.dot{width:8px;height:8px;border-radius:50%;background:var(--gold);box-shadow:0 0 12px var(--gold)}
.cover{position:relative;min-height:342px;margin:12px 0 10px;border:1px solid #1e242d;border-radius:28px;overflow:hidden;background:linear-gradient(145deg,#0c1015,#010203 68%);box-shadow:0 22px 70px #000}.cover:before{content:"";position:absolute;inset:0;background:linear-gradient(#ffffff05 1px,transparent 1px),linear-gradient(90deg,#ffffff05 1px,transparent 1px);background-size:28px 28px;mask-image:linear-gradient(to bottom,#000,transparent 88%)}
.cover-copy{position:relative;text-align:center;padding-top:24px}.eyebrow{font-size:10px;letter-spacing:3px;color:#778294;font-weight:900}.cover h2{margin:5px 0 0;font-size:29px;letter-spacing:4px}.cover h2 span{color:var(--red)}
.pulse-zone{position:relative;height:218px;display:grid;place-items:center}.ring,.ring2,.ring3{position:absolute;border-radius:50%;border:1px solid #ff253a55;animation:pulse 2.2s ease-out infinite}.ring{width:126px;height:126px}.ring2{width:126px;height:126px;animation-delay:.72s}.ring3{width:126px;height:126px;animation-delay:1.44s}
.core{position:relative;width:130px;height:130px;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle at 45% 35%,#272d35,#090b0f 62%,#000);border:2px solid #3b424d;box-shadow:0 0 18px #ff253a50,0 0 70px #ff253a22,inset 0 0 24px #000;animation:throb 1.35s ease-in-out infinite}
.mosquito{width:106px;height:106px;filter:drop-shadow(0 0 10px #ff253a88);animation:hover 2.8s ease-in-out infinite}.wing{fill:#f7f9ff;stroke:#7c8797;stroke-width:2}.body{fill:#ff253a;stroke:#fff;stroke-width:2}.dark{fill:#06070a;stroke:#d8dde5;stroke-width:2}.money{fill:#fff;font-size:29px;font-weight:1000;text-anchor:middle}
.smoke{position:absolute;width:58px;height:25px;border-radius:50%;background:#aab4c522;filter:blur(9px);bottom:24px;animation:smoke 2s ease-out infinite}.tagline{position:absolute;bottom:13px;left:0;right:0;text-align:center;font-size:11px;color:#a5afbd;letter-spacing:1.4px}
@keyframes pulse{0%{transform:scale(.72);opacity:.8}100%{transform:scale(2.08);opacity:0}}@keyframes throb{0%,100%{transform:scale(.96)}50%{transform:scale(1.045);box-shadow:0 0 28px #ff253a80,0 0 90px #ff253a33,inset 0 0 24px #000}}@keyframes hover{0%,100%{transform:translateY(-3px) rotate(-1deg)}50%{transform:translateY(4px) rotate(1deg)}}@keyframes smoke{0%{transform:translateX(-22px) scale(.5);opacity:0}45%{opacity:1}100%{transform:translateX(48px) scale(1.6);opacity:0}}
.market{background:#080b0f;border:1px solid var(--line);border-radius:15px;padding:11px 12px}.market-head{display:flex;justify-content:space-between;font-size:9px;color:#758196;letter-spacing:1.5px;font-weight:900}#ticker-row{display:flex;gap:17px;overflow-x:auto;white-space:nowrap;padding:9px 0 5px;font-size:13px}.market small{font-size:9px;color:#7e8999}.ok{color:var(--green)!important}.warn{color:var(--gold)!important}.error{color:#ff7580!important}
.account{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:10px 0}.tile{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px}.tile label,.tile small{display:block;color:#7f8a9b;font-size:9px;letter-spacing:1.3px;font-weight:900}.tile b{display:block;margin-top:5px;font-size:18px}.amount-wrap{display:flex;align-items:center;margin-top:4px;font-size:18px;font-weight:900}.amount-wrap input{width:100%;border:0;outline:0;background:transparent;color:white;font:inherit}
.controls{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:9px}.controls button{min-height:58px;border:1px solid transparent;border-radius:16px;color:#fff;font-weight:1000;letter-spacing:1px;font-size:14px}.go{background:linear-gradient(#33e985,#16a75a);box-shadow:0 8px 25px #1fd06d25}.stop{background:linear-gradient(#ff3c50,#be1527)}.exit{background:#11151b;border-color:#3a424e!important;color:#ff7580!important}button:active{transform:scale(.97)}
#message{min-height:34px;margin:10px 2px;color:#96a2b3;font-size:11px;text-align:center}.card{background:#080b0f;border:1px solid var(--line);border-radius:17px;padding:13px}.cardhead{display:flex;justify-content:space-between;align-items:center}.cardhead h3{font-size:12px;letter-spacing:1.6px;margin:2px}.cardhead span{font-size:9px;color:var(--green);font-weight:900}.picks{max-height:360px;overflow:auto}.picks div{display:grid;grid-template-columns:28px 1fr 66px 52px;align-items:center;border-top:1px solid #1d232b;padding:9px 3px;font-size:12px}.picks b{color:#647083}.picks span,.picks small{text-align:right}.picks small{color:#8290a3}footer{text-align:center;color:#586273;font-size:9px;padding:16px}
@media(max-width:390px){.cover{min-height:325px}.pulse-zone{height:204px}.controls{grid-template-columns:1fr 1fr 1fr}.controls button{font-size:12px}.account{grid-template-columns:1fr 1fr}}
@media(prefers-reduced-motion:reduce){.ring,.ring2,.ring3,.core,.mosquito,.smoke{animation:none}}
</style>
</head>
<body><main>
<header class="top"><div class="brand"><div class="mini">$</div><div><h1>MOSQUITO</h1><p>V5.8 MASTER</p></div></div><div class="status"><i class="dot" id="connection-dot"></i><span id="connection">CONNECTING</span></div></header>
<section class="cover"><div class="cover-copy"><div class="eyebrow">AI MARKET HUNTER</div><h2>MOSQUITO <span>RUNNER</span></h2></div><div class="pulse-zone"><i class="ring"></i><i class="ring2"></i><i class="ring3"></i><i class="smoke"></i><div class="core">
<svg class="mosquito" viewBox="0 0 160 160" aria-label="Mosquito logo" role="img"><ellipse class="wing" cx="53" cy="51" rx="37" ry="18" transform="rotate(-30 53 51)"/><ellipse class="wing" cx="107" cy="51" rx="37" ry="18" transform="rotate(30 107 51)"/><path class="dark" d="M75 49 58 24M85 49l18-25M68 88 35 111M92 88l33 23M70 103l-20 33M90 103l20 33"/><ellipse class="body" cx="80" cy="87" rx="22" ry="43"/><circle class="dark" cx="80" cy="48" r="16"/><path class="dark" d="M80 35 84 8 89 5"/><text class="money" x="80" y="98">$</text></svg>
</div></div><div class="tagline">SUCKING THE PROFIT OUT OF THE MARKET</div></section>
<section class="market"><div class="market-head"><b>LIVE MARKET PULSE</b><span id="ticker-source">CONNECTING</span></div><div id="ticker-row">Loading Alpaca quotes…</div><small id="ticker-state">Waiting for market data</small></section>
<section class="account"><div class="tile"><small>ALPACA ACCOUNT</small><b id="equity">Checking…</b></div><div class="tile"><label for="trade-amount">RUN AMOUNT</label><div class="amount-wrap">$<input id="trade-amount" inputmode="decimal" type="number" min="0" step="any" placeholder="Enter amount"></div></div></section>
<section class="controls"><button class="go" onclick="startRunner()">GO</button><button class="stop" onclick="stopBot()">STOP</button><button class="exit" onclick="exitRunner()">EXIT</button></section>
<p id="message">Ready. Enter any run amount and press GO.</p>
<section class="card"><div class="cardhead"><h3>V5.8 WATCHLIST</h3><span id="gate">READY</span></div><div id="picks" class="picks"></div></section>
<footer>PAPER MODE · V5.8 MASTER · ORDER EXECUTION REMAINS PROTECTED</footer>
</main>
<script>
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number(n||0));
async function updateAccount(){try{const r=await fetch('/api/account',{cache:'no-store'}),d=await r.json();if(!r.ok||!d.connected)throw Error(d.message||'Not connected');document.getElementById('equity').textContent=money(d.equity);document.getElementById('connection').textContent='ALPACA CONNECTED';document.getElementById('connection-dot').style.background='#29e27d';document.getElementById('connection-dot').style.boxShadow='0 0 12px #29e27d'}catch(e){document.getElementById('equity').textContent='Unavailable';document.getElementById('connection').textContent='CHECK ALPACA';}}
async function updateTicker(){const s=document.getElementById('ticker-state');try{const r=await fetch('/api/quotes',{cache:'no-store'}),d=await r.json();if(!r.ok)throw Error(d.error||'Quote request failed');document.getElementById('ticker-source').textContent=d.source==='alpaca'?'ALPACA LIVE':'MARKET DATA';document.getElementById('ticker-row').innerHTML=d.quotes.map(q=>'<span><b>'+q.symbol+'</b> ():
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
+Number(q.price).toFixed(2)+'</span>').join('');s.textContent='Updated '+new Date(d.generated_at).toLocaleTimeString()+(d.warning?' · backup feed':'');s.className=d.warning?'warn':'ok'}catch(e){s.textContent='Ticker error · '+e.message;s.className='error'}}
async function scan(count=50){const m=document.getElementById('message');m.textContent='Mosquito is scanning the AI market…';document.getElementById('picks').innerHTML='';try{const r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count})}),d=await r.json();if(!r.ok)throw Error(d.error||'Scan failed');m.textContent='Loaded '+d.count+' V5.8 picks · '+new Date(d.generated_at).toLocaleString();document.getElementById('gate').textContent=d.april_trade_allowed?'SIGNAL: GO':'APRIL GATE: STOP';document.getElementById('picks').innerHTML=d.picks.map(x=>'<div><b>'+x.rank+'</b><strong>'+x.ticker+'</strong><span>'+x.score+'</span><small>'+x.weight_pct+'%</small></div>').join('')}catch(e){m.textContent=e.message}}
function startRunner(){const v=document.getElementById('trade-amount').value;document.getElementById('message').textContent=v?'Run amount '+money(v)+' selected. Building V5.8 watchlist…':'Building V5.8 watchlist…';document.getElementById('gate').textContent='SCANNING';scan(50)}
function stopBot(){document.getElementById('message').textContent='Runner stopped. No new action will be started.';document.getElementById('gate').textContent='STOPPED'}
function exitRunner(){document.getElementById('message').textContent='Exit protection selected. Order execution is disabled in this build.';document.getElementById('gate').textContent='EXIT SAFE'}
updateAccount();updateTicker();setInterval(updateTicker,30000);setInterval(updateAccount,60000);
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
