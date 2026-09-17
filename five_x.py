"""Independent virtual-paper ledger for Mosquito's speculative 5X research test.

The ledger intentionally never submits broker orders.  Alpaca nets positions by
symbol, so a second strategy in the same paper account would corrupt both tests.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


PATH = Path(os.getenv("MOSQUITO_5X_FILE", "/data/mosquito-5x.json"))
LOCK = threading.RLock()
POSITION_COUNT = 7


def _now():
    return datetime.now(timezone.utc).isoformat()


def _empty():
    return {"strategy": "Mosquito 5X Candidate Model", "mode": "virtual-paper",
            "target_horizon": "12 months", "target_multiple": 5.0,
            "guaranteed": False, "allocation": 0.0, "cash": 0.0,
            "selection_session": None, "selected": [], "positions": {},
            "closed": [], "status": "standby", "last_error": None,
            "updated_at": None}


def load():
    with LOCK:
        try:
            raw = json.loads(PATH.read_text())
        except (OSError, ValueError, TypeError):
            raw = {}
        return {**_empty(), **(raw if isinstance(raw, dict) else {})}


def save(value):
    with LOCK:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".mosquito-5x-", dir=PATH.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, separators=(",", ":"), sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, PATH)
        finally:
            try: os.unlink(name)
            except FileNotFoundError: pass


def rank_market(close, volume, *, count=50):
    """Rank liquid asymmetric candidates using completed daily bars only."""
    close = close.sort_index().replace([np.inf, -np.inf], np.nan)
    volume = volume.reindex(close.index).replace([np.inf, -np.inf], np.nan)
    if len(close) < 127:
        raise RuntimeError("5X research needs at least 127 completed sessions")
    valid = [s for s in close if close[s].dropna().shape[0] >= 127]
    if not valid:
        raise RuntimeError("No 5X candidates have six months of history")
    px = close[valid].ffill(); vol = volume[valid].fillna(0); latest = px.iloc[-1]
    adv20 = (px.tail(20) * vol.tail(20)).mean()
    median_volume = vol.tail(20).median()
    frame = pd.DataFrame(index=valid)
    frame["price"] = latest
    frame["return_6m"] = latest / px.iloc[-127] - 1
    frame["return_3m"] = latest / px.iloc[-64] - 1
    frame["return_1m"] = latest / px.iloc[-22] - 1
    frame["return_5d"] = latest / px.iloc[-6] - 1
    frame["distance_52w_high"] = latest / px.tail(127).max() - 1
    frame["volume_acceleration"] = vol.tail(20).mean() / vol.tail(60).mean().replace(0, np.nan) - 1
    frame["volatility"] = px.pct_change(fill_method=None).tail(60).std()
    frame["adv20_dollars"] = adv20
    frame["median_volume20"] = median_volume
    # Hard liquidity/history gates. Fundamentals and filing evidence are applied
    # to this ranked shortlist by the caller before anything can be selected.
    frame = frame[(frame.price >= 2) & (frame.price <= 100) &
                  (frame.adv20_dollars >= 2_000_000) &
                  (frame.median_volume20 >= 100_000)].dropna()
    if frame.empty:
        return []
    pct = lambda s: s.rank(pct=True).fillna(.5)
    # Favor sustained acceleration and controlled volatility; explicitly punish
    # one-month blow-offs that make a further 5X less feasible.
    blowoff = (frame.return_1m > .35).astype(float)
    frame["score"] = (30*pct(frame.return_6m) + 20*pct(frame.return_3m) +
                      15*pct(frame.return_1m) + 10*pct(frame.return_5d) +
                      10*pct(frame.volume_acceleration) +
                      10*pct(frame.distance_52w_high) +
                      5*(1-pct(frame.volatility)) - 50*blowoff)
    frame["ticker"] = frame.index.astype(str)
    frame = frame.sort_values(["score", "ticker"], ascending=[False, True]).head(max(1, int(count)))
    rows = []
    for rank, (_, row) in enumerate(frame.iterrows(), 1):
        rows.append({"rank": rank, "ticker": row.ticker, "score": round(float(row.score), 4),
                     "price": round(float(row.price), 6), "eligible": True,
                     "metrics": {k: round(float(row[k]), 8) for k in
                         ("return_6m", "return_3m", "return_1m", "return_5d",
                          "distance_52w_high", "volume_acceleration", "volatility",
                          "adv20_dollars", "median_volume20")}})
    return rows


def lock_selection(session_date, rows, allocation):
    state = load()
    if state.get("selection_session") == session_date and state.get("selected"):
        return state
    selected = [dict(row) for row in rows[:POSITION_COUNT]]
    state.update(selection_session=session_date, selected=selected,
                 allocation=float(allocation), cash=float(allocation), positions={},
                 closed=[], status="locked" if selected else "cash_only",
                 last_error=None, locked_at=_now(), updated_at=_now())
    save(state); return state


def buy_locked(session_date, quotes):
    state = load()
    if state.get("selection_session") != session_date:
        raise RuntimeError("5X selection is not locked for this session")
    if state.get("positions") or state.get("status") == "running":
        return state
    selected = state.get("selected") or []
    weight = float(state.get("allocation") or 0) / POSITION_COUNT
    positions, spent = {}, 0.0
    for row in selected:
        symbol = row["ticker"]; price = float(quotes.get(symbol) or 0)
        if price <= 0 or weight <= 0: continue
        qty = weight / price; spent += weight
        positions[symbol] = {"symbol": symbol, "qty": qty, "entry_price": price,
            "current_price": price, "peak_price": price, "market_value": weight,
            "unrealized_pl": 0.0, "status": "WAITING_FOR_GAIN", "bought_at": _now()}
    state.update(positions=positions, cash=max(0.0, float(state["allocation"])-spent),
                 status="running" if positions else "cash_only", bought_at=_now(),
                 updated_at=_now())
    save(state); return state


def mark(quotes, *, trailing_drop=.0005):
    state = load(); positions = dict(state.get("positions") or {}); closed = list(state.get("closed") or [])
    for symbol, lot in list(positions.items()):
        price = float(quotes.get(symbol) or 0)
        if price <= 0: continue
        entry = float(lot["entry_price"]); peak = max(float(lot.get("peak_price") or entry), price)
        qty = float(lot["qty"]); armed = peak > entry
        # Match Mosquito: trail by 0.05%, but never record a sale below entry.
        if armed and price <= peak*(1-trailing_drop) and price >= entry:
            proceeds = qty*price
            closed.append({**lot, "current_price": price, "exit_price": price,
                           "market_value": proceeds, "realized_pl": proceeds-qty*entry,
                           "sold_at": _now(), "status": "SOLD_PROTECTED"})
            state["cash"] = float(state.get("cash") or 0)+proceeds
            del positions[symbol]; continue
        lot.update(current_price=price, peak_price=peak, market_value=qty*price,
                   unrealized_pl=qty*(price-entry),
                   status="TRAILING" if armed else ("PROTECTED_BELOW_ENTRY" if price < entry else "WAITING_FOR_GAIN"))
    state.update(positions=positions, closed=closed, updated_at=_now())
    save(state); return state


def snapshot():
    state = load(); positions = list((state.get("positions") or {}).values())
    value = float(state.get("cash") or 0)+sum(float(p.get("market_value") or 0) for p in positions)
    allocation = float(state.get("allocation") or 0)
    return {**state, "positions": positions, "current_value": value,
            "profit": value-allocation, "return_pct": ((value/allocation-1)*100 if allocation else None),
            "positions_count": len(positions), "closed_count": len(state.get("closed") or [])}
