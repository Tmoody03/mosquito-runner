"""Paper-only order lifecycle for the Mosquito V5.8 strategy.

The module deliberately knows nothing about Flask or Alpaca request classes.  A
broker adapter supplies ``submit_order(dict)`` and, for reconciliation,
``get_order_by_client_id(id)``.  This keeps the policy testable and makes the
paper/live boundary explicit at one small interface.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


class LifecycleError(RuntimeError):
    pass


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


class Lifecycle:
    """Persistent, idempotent V5.8 paper-entry and replacement coordinator."""

    STRATEGY_REVISION = "green-ribbon-2026-09-16-v1"

    def __init__(self, broker, state_path, clock, *, portfolio_size=50,
                 rebuy_cooldown=timedelta(days=1), rebuy_momentum=0.001,
                 rebuy_confirmations=2, max_price_age=timedelta(seconds=90)):
        self.broker = broker
        self.path = Path(state_path)
        self.clock = clock
        self.portfolio_size = int(portfolio_size)
        self.rebuy_cooldown = rebuy_cooldown
        self.rebuy_momentum = float(rebuy_momentum)
        self.rebuy_confirmations = int(rebuy_confirmations)
        self.max_price_age = max_price_age
        if self.portfolio_size != 50:
            raise LifecycleError("automated lifecycle is locked to 50 positions")
        if self.rebuy_momentum < 0 or self.rebuy_momentum > 0.10:
            raise LifecycleError("rebuy momentum is outside the safe range")
        if self.rebuy_confirmations < 2:
            raise LifecycleError("rebuy requires at least two confirmations")

    def _now(self):
        value = self.clock.now()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _assert_paper(self):
        # Fail closed.  An adapter must affirmatively identify itself as paper.
        paper = getattr(self.broker, "paper", None)
        if callable(paper):
            paper = paper()
        if paper is not True:
            raise LifecycleError("paper broker required; live orders are prohibited")

    def _load(self):
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError, TypeError):
            raw = {}
        # A strategy revision starts a new *local paper-test generation*.  This
        # prevents symbols retired by an obsolete exit rule from blocking the
        # corrected rule. Broker positions/orders remain authoritative and are
        # never altered by this migration.
        if raw and raw.get("strategy_revision") != self.STRATEGY_REVISION:
            raw = {}
        return {
            "strategy_revision": self.STRATEGY_REVISION,
            "orders": dict(raw.get("orders") or {}),
            "positions": dict(raw.get("positions") or {}),
            "retired": dict(raw.get("retired") or {}),
            "events": list(raw.get("events") or []),
        }

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".lifecycle-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _ranked(picks):
        rows = sorted(picks, key=lambda row: (row.get("rank", 10**9),
                                               -float(row.get("score", 0))))
        result, seen = [], set()
        for row in rows:
            symbol = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
            price = float(row.get("price") or 0)
            if symbol and symbol not in seen and price > 0 and row.get("eligible", True):
                seen.add(symbol)
                result.append({**row, "symbol": symbol, "price": price})
        return result

    def _client_id(self, purpose, symbol, generation=0):
        # Stable across process restarts and retries within a market session.
        day = self._now().date().isoformat().replace("-", "")
        if purpose == "entry":
            # A reset clears Mosquito's local lifecycle state, not Alpaca's
            # historical client-order IDs. Namespace new entry IDs so a same-day
            # reset cannot be rejected as a duplicate while retries stay stable.
            namespace = (os.getenv("MOSQUITO_ORDER_NAMESPACE") or
                         os.getenv("MOSQUITO_RESET_ID") or "default")
            digest = hashlib.sha256(
                f"{namespace}:{self.STRATEGY_REVISION}:{generation}".encode("utf-8")
            ).hexdigest()[:6]
            return f"mosquito-v58-{purpose}-{day}-{symbol}-{digest}"
        return f"mosquito-v58-{purpose}-{day}-{symbol}-{generation}"

    def _submit_once(self, state, *, symbol, notional, purpose, generation=0):
        client_id = self._client_id(purpose, symbol, generation)
        if client_id in state["orders"]:
            return state["orders"][client_id]
        order = self.broker.get_order_by_client_id(client_id)
        if order is None:
            request = {"symbol": symbol, "notional": round(float(notional), 2),
                       "side": "buy", "type": "market", "time_in_force": "day",
                       "client_order_id": client_id}
            order = self.broker.submit_order(request)
        else:
            request = {"notional": round(float(notional), 2)}
        raw_status=getattr(order,"status","submitted")
        status=str(getattr(raw_status,"value",raw_status))
        row = {"client_order_id": client_id, "broker_order_id": str(getattr(order, "id", "")),
               "symbol": symbol, "purpose": purpose, "status": status,
               "requested_notional": request["notional"], "submitted_at": _iso(self._now()),
               "filled_qty": 0.0, "filled_avg_price": None}
        state["orders"][client_id] = row
        state["events"].append({"type": "BUY_SUBMITTED", "symbol": symbol,
                                "client_order_id": client_id, "at": _iso(self._now())})
        return row

    def enter(self, picks, buying_power):
        """Enter the top 50, equally weighted, once the injected clock is open."""
        self._assert_paper()
        if not self.clock.is_market_open():
            raise LifecycleError("market is closed")
        ranked = self._ranked(picks)
        if len(ranked) < self.portfolio_size:
            raise LifecycleError("V5.8 supplied fewer than 50 eligible picks")
        amount = float(buying_power)
        if amount <= 0:
            raise LifecycleError("buying power must be positive")
        state = self._load()
        weight = amount / self.portfolio_size
        for row in ranked[:self.portfolio_size]:
            symbol = row["symbol"]
            if symbol in state["positions"]:
                continue
            self._submit_once(state, symbol=symbol, notional=weight, purpose="entry")
        self._save(state)
        return state

    def enter_available(self, picks, buying_power, *, total_budget):
        """Submit equal-weight entries only for currently confirmed locked picks.

        Unlike ``enter``, this supports a staged launch: the Top 50 is locked first,
        and each name receives exactly one order when its live entry signal arrives.
        """
        self._assert_paper()
        if not self.clock.is_market_open():
            raise LifecycleError("market is closed")
        ranked = self._ranked(picks)
        state = self._load()
        pending_statuses = {"new", "accepted", "pending_new", "partially_filled", "submitted"}
        occupied = set(state["positions"]) | {o["symbol"] for o in state["orders"].values()
            if str(o.get("status", "")).lower() in pending_statuses}
        slots = max(0, self.portfolio_size - len(occupied))
        weight = float(total_budget) / self.portfolio_size
        available = max(0.0, float(buying_power))
        for row in ranked:
            if slots <= 0 or available < 1:
                break
            symbol = row["symbol"]
            # A verified round trip is retired for this staged launch. Re-entry
            # belongs to the cooldown/baseline rebalance path, not a duplicate
            # generation-zero order in the same scheduler cycle.
            if symbol in occupied or symbol in state["retired"]:
                continue
            notional = min(weight, available)
            self._submit_once(state, symbol=symbol, notional=notional, purpose="entry")
            occupied.add(symbol); slots -= 1; available -= notional
        self._save(state)
        return state

    def reconcile(self):
        """Copy actual broker fills into durable lots; never infer a fill."""
        self._assert_paper()
        state = self._load()
        for client_id, local in state["orders"].items():
            remote = self.broker.get_order_by_client_id(client_id)
            if remote is None:
                continue
            raw_status=getattr(remote,"status",local["status"])
            status=str(getattr(raw_status,"value",raw_status))
            qty = float(getattr(remote, "filled_qty", 0) or 0)
            price_raw = getattr(remote, "filled_avg_price", None)
            price = float(price_raw) if price_raw not in (None, "") else None
            local.update(status=status, filled_qty=qty, filled_avg_price=price,
                         reconciled_at=_iso(self._now()))
            if qty > 0 and price:
                symbol = local["symbol"]
                state["positions"][symbol] = {"symbol": symbol, "qty": qty,
                    "entry_price": price, "entry_at": local.get("submitted_at"),
                    "client_order_id": client_id, "status": status}
        self._save(state)
        return state

    def record_exit(self, symbol, *, fill_price, filled_at=None):
        """Fill hook used by the protected sell monitor."""
        self._assert_paper()
        state = self._load(); symbol = symbol.upper()
        lot = state["positions"].pop(symbol, None)
        if not lot:
            return state
        when = filled_at or self._now()
        state["retired"][symbol] = {**lot, "exit_price": float(fill_price),
            "baseline": float(fill_price), "exited_at": _iso(when),
            "watch_price": float(fill_price), "watch_price_at": _iso(when),
            "watch_status": "COOLDOWN", "upward_ticks": 0,
            "generation": int(state["retired"].get(symbol, {}).get("generation", 0)) + 1}
        self._save(state)
        return state

    def rebalance(self, picks, prices, buying_power, *, dead_symbols=()):
        """Replace dead/ineligible names and rebuy renewed post-exit momentum.

        Every profitably sold symbol remains in the retired watch list.  Once its
        cooldown has elapsed, a fresh price rise from the prior watch observation
        qualifies it for re-entry.  This deliberately permits a rebuy above the
        exit price when a runner resumes climbing; an idempotent generation key
        prevents duplicate orders. Dead symbols are never selected again in this
        cycle; the next ranked eligible candidate takes the vacant slot.
        """
        self._assert_paper()
        if not self.clock.is_market_open():
            raise LifecycleError("market is closed")
        state = self._load(); now = self._now()
        ranked = self._ranked(picks); eligible = {r["symbol"] for r in ranked}
        dead = {s.upper() for s in dead_symbols}
        for symbol in list(state["positions"]):
            if symbol in dead or symbol not in eligible:
                # Removal is a selection-state transition only. The protected
                # sell engine must provide the actual fill through record_exit.
                state["positions"][symbol]["replacement_pending"] = True
        active = {s for s, p in state["positions"].items() if not p.get("replacement_pending")}
        pending_statuses = {"new", "accepted", "pending_new", "partially_filled", "submitted"}
        pending_buys = {o["symbol"] for o in state["orders"].values()
                        if o["status"].lower() in pending_statuses and
                        o["symbol"] not in state["positions"]}
        vacancies = max(0, self.portfolio_size - len(active | pending_buys))
        # Keep a sold runner's slot available while its watch signal develops.
        # Otherwise a lower-ranked replacement would consume the vacancy during
        # cooldown and the watched symbol could never be bought back.
        watched = {s for s in state["retired"] if s in eligible and s not in dead
                   and s not in active and s not in pending_buys}
        notional = float(buying_power) / vacancies if vacancies else 0
        for row in ranked:
            if vacancies <= 0:
                break
            symbol = row["symbol"]
            if symbol in dead or symbol in active or symbol in pending_buys:
                continue
            retired = state["retired"].get(symbol)
            purpose, generation = "replacement", 0
            if not retired and watched and vacancies <= len(watched):
                continue
            if retired:
                exited = datetime.fromisoformat(retired["exited_at"])
                observation = prices.get(symbol)
                if isinstance(observation, dict):
                    current = float(observation.get("price") or 0)
                    observed_at = observation.get("observed_at")
                    try:
                        observed_at = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
                        if observed_at.tzinfo is None:
                            observed_at = observed_at.replace(tzinfo=timezone.utc)
                    except (TypeError, ValueError):
                        observed_at = None
                else:
                    current, observed_at = float(observation or 0), None
                previous = float(retired.get("watch_price") or retired["baseline"])
                if now < exited + self.rebuy_cooldown:
                    retired.update(watch_status="COOLDOWN", upward_ticks=0)
                    continue
                prior_at = retired.get("watch_price_at")
                prior_at = datetime.fromisoformat(prior_at) if prior_at else None
                fresh = (observed_at is not None and observed_at <= now and
                         now - observed_at <= self.max_price_age and
                         (prior_at is None or observed_at > prior_at))
                if current <= 0 or not fresh:
                    retired["watch_status"] = "STALE_PRICE" if current > 0 else "PRICE_UNAVAILABLE"
                    continue
                renewed = current >= previous * (1 + self.rebuy_momentum)
                confirmations = int(retired.get("upward_ticks", 0)) + 1 if renewed else 0
                retired.update(watch_price=current, watch_price_at=_iso(observed_at),
                               watch_status=("REENTRY_SIGNAL" if confirmations >= self.rebuy_confirmations
                                             else "CONFIRMING" if renewed else "WATCHING"),
                               upward_ticks=confirmations)
                if confirmations < self.rebuy_confirmations:
                    continue
                purpose, generation = "rebuy", int(retired["generation"])
            self._submit_once(state, symbol=symbol, notional=notional,
                              purpose=purpose, generation=generation)
            pending_buys.add(symbol); vacancies -= 1; watched.discard(symbol)
        self._save(state)
        return state
