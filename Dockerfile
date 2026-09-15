FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir Flask==3.1.2 gunicorn==23.0.0 numpy==2.3.3 pandas==2.3.2 yfinance==0.2.65 alpaca-py==0.42.2

RUN <<'PY'
from pathlib import Path

Path('/app/app.py').write_text(r'''import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, jsonify, request

app = Flask(__name__)

AI_UNIVERSE = sorted(set("""
AAPL AMD AMAT ANET ASML AVGO CDNS CLS COHR CRDO CRM CSCO DELL EQIX FSLR GEV GLW
GOOG GOOGL HPE IBM INTC LITE LRCX META MRVL MSFT MU NBIS NEE NFLX NOW NVDA NXPI
ORCL PANW PLTR QCOM ROK ROP SMCI SNPS TSM VRT WDC WDAY ZS
""".split()))

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mosquito V5.8 Master</title><style>
:root{--red:#ed1c24;--blue:#1572e8;--ink:#101827}*{box-sizing:border-box}body{margin:0;background:linear-gradient(160deg,#061329,#112b55);font-family:system-ui,-apple-system,sans-serif;color:white;min-height:100vh}main{max-width:760px;margin:auto;padding:24px 16px 40px}header{display:flex;align-items:center;gap:12px}h1{margin:0;letter-spacing:3px}.bug{height:58px;width:58px;border-radius:50%;background:var(--red);display:grid;place-items:center;font-size:34px;font-weight:900;box-shadow:0 0 0 5px #fff}.hero{margin:24px 0 14px;background:linear-gradient(135deg,#fff,#dbe8f8);color:var(--ink);padding:22px;border-radius:22px}.hero span{background:var(--red);color:#fff;font-weight:900;padding:5px 9px;border-radius:7px}.hero h2{font-size:32px;line-height:1.02}.stats,.controls{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.stats div{background:#fff;padding:10px;border-radius:10px}.stats small{display:block;color:#65738b}button{border:0;border-radius:12px;padding:16px 8px;font-weight:900;color:#fff;background:var(--blue)}button.go{background:#13a44b}button.stop{background:var(--red)}.card{background:#fff;color:var(--ink);border-radius:18px;padding:15px}.pick{display:grid;grid-template-columns:34px 1fr 70px 58px;border-top:1px solid #e7ecf2;padding:10px 4px}.pick span,.pick small{text-align:right}footer{text-align:center;color:#9bb0c9;padding:20px;font-size:11px}
</style></head><body><main><header><div class="bug">$</div><div><h1>MOSQUITO</h1><b>V5.8 MASTER</b></div></header><section class="hero"><span>PAPER PREVIEW</span><h2>Sucking the profit<br>out of the market.</h2><div class="stats"><div><small>STRATEGY</small><b>Top 50</b></div><div><small>WEIGHT</small><b>Equal</b></div><div><small>UNIVERSE</small><b>AI Only</b></div></div></section><section class="controls"><button class="go" onclick="scan(50)">GO · LOAD 50</button><button onclick="scan(25)">VIEW 25</button><button class="stop" onclick="stopBot()">STOP</button></section><p id="message">Ready to generate the next V5.8 Master watchlist.</p><section class="card"><h3>V5.8 WATCHLIST <span id="gate"></span></h3><div id="picks"></div></section><footer>Research and Alpaca paper-preview only · Verify Fidelity eligibility</footer></main><script>
async function scan(count){let m=document.getElementById('message');m.textContent='Scanning live market data…';document.getElementById('picks').innerHTML='';try{let r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count})});let d=await r.json();if(!r.ok)throw Error(d.error||'Scan failed');m.textContent=`Generated ${d.count} picks · ${new Date(d.generated_at).toLocaleString()}`;document.getElementById('gate').textContent=d.april_trade_allowed?' · SIGNAL: GO':' · APRIL GATE: STOP';document.getElementById('picks').innerHTML=d.picks.map(x=>`<div class="pick"><b>${x.rank}</b><strong>${x.ticker}</strong><span>${x.score}</span><small>${x.weight_pct}%</small></div>`).join('')}catch(e){m.textContent=e.message}}function stopBot(){document.getElementById('message').textContent='Stopped. No orders are being submitted.'}
</script></body></html>"""

def rank01(s):
    return s.rank(pct=True).fillna(0.5)

def watchlist(count):
    data = yf.download(AI_UNIVERSE, period='5y', interval='1d', auto_adjust=True, progress=False, group_by='column', threads=True)
    if data.empty: raise RuntimeError('Market data is temporarily unavailable')
    close, volume = data['Close'], data['Volume']
    eligible = [t for t in close.columns if close[t].dropna().shape[0] >= 1008]
    close, volume = close[eligible].ffill(), volume[eligible].fillna(0)
    latest = close.iloc[-1]
    frame = pd.DataFrame({
        'm1': latest/close.iloc[-22]-1, 'm3': latest/close.iloc[-66]-1,
        'm6': latest/close.iloc[-132]-1, 'm12': latest/close.iloc[-252]-1,
        'breakout': latest/close.tail(252).max()-1,
        'volume': volume.tail(22).mean()/volume.tail(66).mean()-1,
        'quality': close.pct_change().tail(252).mean()/close.pct_change().tail(252).std()
    }).replace([np.inf,-np.inf],np.nan).dropna()
    frame['score'] = .10*rank01(frame.m1)+.15*rank01(frame.m3)+.25*rank01(frame.m6)+.20*rank01(frame.m12)+.12*rank01(frame.breakout)+.08*rank01(frame.volume)+.10*rank01(frame.quality)
    picks = frame.sort_values('score',ascending=False).head(count)
    positive = float(frame.m1.median()) > 0
    return {'count':len(picks),'positive_signal':positive,'april_trade_allowed':datetime.now(timezone.utc).month != 4 or positive,'generated_at':datetime.now(timezone.utc).isoformat(),'picks':[{'rank':i+1,'ticker':t,'score':round(float(r.score)*100,2),'weight_pct':round(100/len(picks),2)} for i,(t,r) in enumerate(picks.iterrows())]}

@app.get('/')
def home(): return PAGE

@app.get('/health')
def health(): return jsonify(status='ok',strategy='V5.8 Master')

@app.post('/api/scan')
def scan():
    try:
        count=max(25,min(int((request.get_json(silent=True) or {}).get('count',50)),50))
        return jsonify(watchlist(count))
    except Exception as exc: return jsonify(error=str(exc)),503

@app.get('/api/account')
def account():
    key,secret=os.getenv('ALPACA_API_KEY'),os.getenv('ALPACA_SECRET_KEY')
    if not key or not secret: return jsonify(connected=False,mode='paper',message='Add Alpaca paper keys in Railway Variables')
    try:
        from alpaca.trading.client import TradingClient
        acct=TradingClient(key,secret,paper=True).get_account()
        return jsonify(connected=True,mode='paper',equity=float(acct.equity),buying_power=float(acct.buying_power))
    except Exception as exc: return jsonify(connected=False,mode='paper',message=str(exc)),503
''')
PY

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 180"]
