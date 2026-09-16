"""Durable paper-account trailing monitor for Mosquito."""
from __future__ import annotations

import json, os, tempfile, threading, time
from datetime import datetime, timezone
from pathlib import Path

from protection import protected_state

PATH=Path(os.getenv("MOSQUITO_ENGINE_FILE","/data/mosquito-engine.json"))
LOCK=threading.RLock()
EMPTY={"positions":{},"events":[],"last_cycle":None,"last_error":None}


def _now(): return datetime.now(timezone.utc).isoformat()


def load():
    with LOCK:
        try:data=json.loads(PATH.read_text())
        except (OSError,ValueError):data={}
        return {**EMPTY,**data,"positions":dict(data.get("positions") or {}),"events":list(data.get("events") or [])}


def save(data):
    with LOCK:
        PATH.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix=".mosquito-engine-",dir=str(PATH.parent))
        try:
            with os.fdopen(fd,"w") as f:json.dump(data,f,separators=(",",":"))
            os.replace(name,PATH)
        finally:
            try:os.unlink(name)
            except FileNotFoundError:pass


def _num(value):
    try:return float(value)
    except (TypeError,ValueError):return None


def cycle(broker, submit_enabled=False):
    """Update peaks and submit idempotent protected paper limit sells."""
    data=load(); old=data["positions"]; fresh={}; events=data["events"][-199:]
    current_positions=broker.get_all_positions()
    from alpaca.trading.enums import OrderSide,TimeInForce
    from alpaca.trading.requests import LimitOrderRequest
    for p in current_positions:
        symbol=str(getattr(p,"symbol",""));qty=_num(getattr(p,"qty",None));entry=_num(getattr(p,"avg_entry_price",None));current=_num(getattr(p,"current_price",None))
        if not symbol or not qty or qty<=0 or not entry or not current:continue
        prior=old.get(symbol,{})
        # A changed entry means a new/adjusted lot; never reuse an unrelated peak.
        prior_peak=prior.get("peak_price") if abs(float(prior.get("entry_price",entry))-entry)<1e-8 else None
        guard=protected_state(entry,current,prior_peak)
        row={"symbol":symbol,"qty":qty,**guard,"updated_at":_now(),"pending_order_id":prior.get("pending_order_id"),"sell_reason":prior.get("sell_reason")}
        if guard["should_sell"] and submit_enabled and not row["pending_order_id"]:
            request=LimitOrderRequest(symbol=symbol,qty=qty,side=OrderSide.SELL,time_in_force=TimeInForce.DAY,limit_price=guard["protected_floor"],client_order_id=f"mosquito-trail-{symbol}-{int(entry*100)}-{int(time.time())}")
            order=broker.submit_order(order_data=request)
            row["pending_order_id"]=str(getattr(order,"id","submitted"));row["sell_reason"]="TRAIL_0.25_PERCENT_PROTECTED"
            events.append({"type":"SELL_SUBMITTED","symbol":symbol,"qty":qty,"buy_price":entry,"trigger_price":guard["trailing_trigger"],"limit_price":guard["protected_floor"],"peak_price":guard["peak_price"],"order_id":row["pending_order_id"],"timestamp":_now()})
        fresh[symbol]=row
    closed=set(old)-set(fresh)
    for symbol in closed:
        if old[symbol].get("pending_order_id"):
            events.append({"type":"POSITION_CLOSED_PENDING_RECONCILIATION","symbol":symbol,"buy_price":old[symbol].get("entry_price"),"peak_price":old[symbol].get("peak_price"),"reason":old[symbol].get("sell_reason"),"timestamp":_now()})
    data.update(positions=fresh,events=events[-200:],last_cycle=_now(),last_error=None);save(data);return data


def public_state():
    data=load();return {"positions":list(data["positions"].values()),"events":data["events"],"last_cycle":data["last_cycle"],"last_error":data["last_error"]}
