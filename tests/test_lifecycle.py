from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lifecycle import Lifecycle, LifecycleError


class Clock:
    def __init__(self, opened=True):
        self.value = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)
        self.opened = opened
    def now(self): return self.value
    def is_market_open(self): return self.opened


class Broker:
    paper = True
    def __init__(self): self.submitted = []; self.remote = {}
    def submit_order(self, request):
        self.submitted.append(request)
        order = SimpleNamespace(id=f"id-{len(self.submitted)}", status="accepted")
        self.remote[request["client_order_id"]] = order
        return order
    def get_order_by_client_id(self, client_id): return self.remote.get(client_id)


def picks(n=60):
    return [{"ticker": f"AI{i:03}", "rank": i, "score": 100-i, "price": 10+i,
             "eligible": True} for i in range(1, n+1)]


def test_top_50_equal_weight_and_restart_idempotency(tmp_path):
    broker, clock = Broker(), Clock(); path = tmp_path/"life.json"
    Lifecycle(broker, path, clock).enter(picks(), 50_000)
    assert len(broker.submitted) == 50
    assert {r["notional"] for r in broker.submitted} == {1000.0}
    assert all(r["client_order_id"].startswith("mosquito-v58-entry-20260917-") for r in broker.submitted)
    Lifecycle(broker, path, clock).enter(picks(), 50_000)
    assert len(broker.submitted) == 50


def test_market_guard_and_paper_only_fail_closed(tmp_path):
    broker, clock = Broker(), Clock(False)
    with pytest.raises(LifecycleError, match="closed"):
        Lifecycle(broker, tmp_path/"x", clock).enter(picks(), 1000)
    broker.paper = False; clock.opened = True
    with pytest.raises(LifecycleError, match="paper"):
        Lifecycle(broker, tmp_path/"x", clock).enter(picks(), 1000)
    assert broker.submitted == []


def test_requires_complete_v58_selection(tmp_path):
    with pytest.raises(LifecycleError, match="fewer than 50"):
        Lifecycle(Broker(), tmp_path/"x", Clock()).enter(picks(49), 50000)


def test_reconcile_records_actual_fill_not_requested_price(tmp_path):
    broker, clock = Broker(), Clock(); life = Lifecycle(broker, tmp_path/"x", clock)
    life.enter(picks(), 50000)
    request = broker.submitted[0]
    broker.remote[request["client_order_id"]] = SimpleNamespace(
        status="filled", filled_qty="7.25", filled_avg_price="137.42")
    state = life.reconcile(); lot = state["positions"][request["symbol"]]
    assert lot["qty"] == 7.25 and lot["entry_price"] == 137.42


def test_dead_name_skipped_for_next_ranked_replacement(tmp_path):
    broker, clock = Broker(), Clock(); life = Lifecycle(broker, tmp_path/"x", clock)
    state = life._load()
    for row in picks(50):
        state["positions"][row["ticker"]] = {"symbol": row["ticker"], "qty": 1, "entry_price": 10}
    life._save(state)
    life.rebalance(picks(60), {"AI051": 61}, 1000, dead_symbols={"AI010"})
    assert broker.submitted[-1]["symbol"] == "AI051"
    assert broker.submitted[-1]["client_order_id"].startswith("mosquito-v58-replacement-")


def test_rebuy_requires_cooldown_and_price_at_or_below_exit_baseline(tmp_path):
    broker, clock = Broker(), Clock(); life = Lifecycle(broker, tmp_path/"x", clock)
    state = life._load()
    for row in picks(49):
        state["positions"][row["ticker"]] = {"symbol": row["ticker"], "qty": 1, "entry_price": 10}
    state["positions"]["AI050"] = {"symbol":"AI050", "qty":1, "entry_price":50}
    life._save(state); life.record_exit("AI050", fill_price=75)
    life.rebalance(picks(50), {"AI050": 70}, 1000)
    assert broker.submitted == []
    clock.value += timedelta(days=1, seconds=1)
    life.rebalance(picks(50), {"AI050": 76}, 1000)
    assert broker.submitted == []  # rebuy above baseline rejected

    # A baseline-qualified rebuy is submitted once despite repeated cycles.
    state = life._load(); state["orders"] = {}; life._save(state)
    life.rebalance(picks(50), {"AI050": 74}, 1000)
    assert broker.submitted[-1]["symbol"] == "AI050"
    count = len(broker.submitted)
    life.rebalance(picks(50), {"AI050": 70}, 1000)
    assert len(broker.submitted) == count
