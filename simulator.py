"""Persistent, paper-only V5.8 basket simulator. Never submits broker orders."""
from __future__ import annotations
import json,os,tempfile,threading
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
from strategy import build_watchlist

PATH=Path(os.getenv("MOSQUITO_SIM_FILE","/tmp/mosquito-simulation.json"));LOCK=threading.RLock()
EMPTY={"active":False,"starting_value":0.0,"cash":0.0,"positions":[],"decision_at":None,"period":None,"rules_version":"V5.8 Master · reconstructed factors","rules_hash":"v58-reconstructed-2026-09","history":[]}

def load():
    with LOCK:
        try:data=json.loads(PATH.read_text())
        except (OSError,ValueError):data={}
        return {**EMPTY,**data}

def save(data):
    with LOCK:
        PATH.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(prefix=".mosquito-sim-",dir=str(PATH.parent))
        try:
            with os.fdopen(fd,"w") as f:json.dump(data,f,separators=(",",":"))
            os.replace(name,PATH)
        finally:
            try:os.unlink(name)
            except FileNotFoundError:pass

def start(amount,count=50):
    amount=float(amount)
    if amount<=0:raise ValueError("Simulation amount must be greater than zero")
    result=build_watchlist(count);picks=result.get("picks",[])
    if not result.get("april_trade_allowed",True):
        data={**EMPTY,"active":True,"starting_value":amount,"cash":amount,"decision_at":result["generated_at"],"period":datetime.now(timezone.utc).strftime("%Y-%m"),"gate":"APRIL_NO_TRADE","history":[]};save(data);return data
    valid=[p for p in picks if float(p.get("price") or 0)>0]
    if not valid:raise RuntimeError("No eligible V5.8 prices were available")
    each=amount/len(valid);positions=[{"symbol":p["ticker"],"qty":each/float(p["price"]),"entry_price":float(p["price"]),"entry_value":each,"rank":p["rank"],"score":p["score"]} for p in valid]
    data={**EMPTY,"active":True,"starting_value":amount,"cash":0.0,"positions":positions,"decision_at":result["generated_at"],"period":datetime.now(timezone.utc).strftime("%Y-%m"),"gate":"OPEN","history":[]};save(data);return data

def stop():
    data=load();data["active"]=False;save(data);return data

def exit_all():
    report=value();data=load();data.update(active=False,cash=float(report.get("current_value") or 0),positions=[]);save(data);return report

def _closes(symbols):
    if not symbols:return pd.DataFrame()
    raw=yf.download(symbols,period="1mo",interval="1d",auto_adjust=True,progress=False,group_by="column",threads=True)
    if raw.empty:return pd.DataFrame()
    close=raw["Close"]
    if isinstance(close,pd.Series):close=close.to_frame(symbols[0])
    return close.ffill().dropna(how="all")

def value():
    data=load();positions=data.get("positions",[]);starting=float(data.get("starting_value") or 0);cash=float(data.get("cash") or 0)
    if not positions:
        return {"status":"FORMING" if data.get("active") else "STOPPED","current_value":cash or starting,"starting_value":starting,"day_profit":0.0,"day_profit_pct":0.0,"ribbon_value":0.0,"ribbon_earned":False,"consecutive_green_closes":0,"eod_value":cash or starting,"eod_as_of":None,"positions_count":0,"rules_version":data["rules_version"],"rules_hash":data["rules_hash"],"gate":data.get("gate")}
    close=_closes([p["symbol"] for p in positions])
    if close.empty:raise RuntimeError("Simulation prices are unavailable")
    qty={p["symbol"]:float(p["qty"]) for p in positions};daily=[]
    for stamp,row in close.iterrows():
        total=cash+sum(qty[s]*float(row[s]) for s in qty if s in row and pd.notna(row[s]));daily.append((stamp,total))
    current=daily[-1][1];previous=daily[-2][1] if len(daily)>1 else starting;day_profit=current-previous;day_pct=(day_profit/previous*100) if previous else 0
    changes=[daily[i][1]-daily[i-1][1] for i in range(1,len(daily))];consecutive=0
    for change in reversed(changes):
        if change>0:consecutive+=1
        else:break
    as_of=daily[-1][0].date().isoformat();now_et=datetime.now(ZoneInfo("America/New_York"));is_today=as_of==now_et.date().isoformat();final=not is_today or now_et.hour>=16
    history=[{"date":d.date().isoformat(),"value":round(v,2)} for d,v in daily[-22:]]
    data["history"]=history;save(data)
    return {"status":"FINAL · MARKET CLOSED" if final else "ACTIVE · MARKET OPEN","current_value":round(current,2),"starting_value":round(starting,2),"day_profit":round(day_profit,2),"day_profit_pct":round(day_pct,4),"ribbon_value":round(max(day_profit,0),2),"ribbon_earned":consecutive>=2,"consecutive_green_closes":consecutive,"eod_value":round(current,2),"eod_as_of":as_of,"positions_count":len(positions),"rules_version":data["rules_version"],"rules_hash":data["rules_hash"],"gate":data.get("gate"),"history":history}
