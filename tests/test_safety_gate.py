"""Safety-gate tests for Mosquito's protected trailing-sale engine.

These tests deliberately use a fake paper broker.  They verify the decision and
persistence boundary without contacting Alpaca or submitting a real order.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import engine
from protection import protected_state


class FakeBroker:
    def __init__(self, positions=()):
        self.positions = list(positions)
        self.submitted = []

    def get_all_positions(self):
        return self.positions

    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        return SimpleNamespace(id=f"paper-order-{len(self.submitted)}")


def position(
    current: float,
    *,
    entry: float = 100.0,
    qty: float = 10.0,
    symbol: str = "NVDA",
    price_timestamp: datetime | None = None,
):
    return SimpleNamespace(
        symbol=symbol,
        qty=str(qty),
        avg_entry_price=str(entry),
        current_price=str(current),
        price_timestamp=price_timestamp,
    )


@pytest.fixture(autouse=True)
def isolated_engine_file(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PATH", tmp_path / "mosquito-engine.json")


def test_no_sale_below_entry_even_after_large_prior_gain():
    guard = protected_state(100.0, 99.99, previous_peak=175.0)
    assert guard["below_entry"] is True
    assert guard["should_sell"] is False
    assert guard["status"] == "PROTECTED_BELOW_ENTRY"
    assert guard["protected_floor"] == 100.0


def test_five_hundredths_percent_trigger_boundary_is_inclusive():
    peak = 110.0
    trigger = peak * 0.9995
    assert protected_state(100.0, trigger + 0.000_002, peak)["should_sell"] is False
    at_boundary = protected_state(100.0, trigger, peak)
    assert at_boundary["trailing_trigger"] == pytest.approx(109.945)
    assert at_boundary["should_sell"] is True


def test_trail_is_not_armed_until_price_exceeds_entry():
    at_cost = protected_state(100.0, 100.0)
    assert at_cost["trail_armed"] is False
    assert at_cost["should_sell"] is False


def test_gap_below_entry_never_releases_an_order():
    broker = FakeBroker([position(80.0)])
    engine.save(
        {
            **engine.EMPTY,
            "positions": {
                "NVDA": {"entry_price": 100.0, "peak_price": 150.0}
            },
        }
    )
    result = engine.cycle(broker, submit_enabled=True)
    assert broker.submitted == []
    assert result["positions"]["NVDA"]["status"] == "PROTECTED_BELOW_ENTRY"


def test_pending_order_prevents_duplicates_during_partial_fill():
    broker = FakeBroker([position(109.70)])
    engine.save(
        {
            **engine.EMPTY,
            "positions": {
                "NVDA": {"entry_price": 100.0, "peak_price": 110.0}
            },
        }
    )
    first = engine.cycle(broker, submit_enabled=True)
    assert len(broker.submitted) == 1
    assert first["positions"]["NVDA"]["pending_order_id"] == "paper-order-1"

    # A broker can report reduced quantity while the same sell remains pending.
    broker.positions = [position(109.60, qty=4)]
    second = engine.cycle(broker, submit_enabled=True)
    assert len(broker.submitted) == 1
    assert second["positions"]["NVDA"]["qty"] == 4.0
    assert second["positions"]["NVDA"]["pending_order_id"] == "paper-order-1"


def test_pending_order_survives_restart_and_still_prevents_duplicates():
    broker = FakeBroker([position(109.70)])
    engine.save(
        {
            **engine.EMPTY,
            "positions": {
                "NVDA": {
                    "entry_price": 100.0,
                    "peak_price": 110.0,
                    "pending_order_id": "existing-order",
                    "sell_reason": "TRAIL_0.10_PERCENT_PROTECTED",
                }
            },
        }
    )
    # cycle() must recover exclusively from the durable file, as a fresh process does.
    result = engine.cycle(broker, submit_enabled=True)
    assert broker.submitted == []
    assert result["positions"]["NVDA"]["pending_order_id"] == "existing-order"


def test_peak_survives_restart_and_triggers_later_protected_sale():
    engine.save(
        {
            **engine.EMPTY,
            "positions": {
                "NVDA": {"entry_price": 100.0, "peak_price": 125.0}
            },
        }
    )
    broker = FakeBroker([position(124.68)])
    result = engine.cycle(broker, submit_enabled=True)
    assert len(broker.submitted) == 1
    assert result["positions"]["NVDA"]["peak_price"] == 125.0
    assert result["positions"]["NVDA"]["protected_floor"] == 100.0


def test_disconnect_does_not_erase_durable_positions_or_create_orders():
    durable = {
        **engine.EMPTY,
        "positions": {"NVDA": {"entry_price": 100.0, "peak_price": 125.0}},
    }
    engine.save(durable)

    class DisconnectedBroker(FakeBroker):
        def get_all_positions(self):
            raise ConnectionError("paper broker unavailable")

    broker = DisconnectedBroker()
    with pytest.raises(ConnectionError):
        engine.cycle(broker, submit_enabled=True)
    assert broker.submitted == []
    assert engine.load()["positions"] == durable["positions"]


def test_stale_price_cannot_trigger_a_sale():
    stale = datetime.now(timezone.utc) - timedelta(minutes=20)
    broker = FakeBroker([position(109.70, price_timestamp=stale)])
    engine.save(
        {
            **engine.EMPTY,
            "positions": {
                "NVDA": {"entry_price": 100.0, "peak_price": 110.0}
            },
        }
    )
    engine.cycle(broker, submit_enabled=True)
    assert broker.submitted == []


def test_out_of_order_price_cannot_trigger_a_sale():
    now = datetime.now(timezone.utc)
    broker = FakeBroker([position(110.0, price_timestamp=now)])
    first = engine.cycle(broker, submit_enabled=True)
    assert first["positions"]["NVDA"]["peak_price"] == 110.0

    # This older sample is below the trail but must not be treated as a new tick.
    broker.positions = [position(109.70, price_timestamp=now - timedelta(seconds=30))]
    engine.cycle(broker, submit_enabled=True)
    assert broker.submitted == []


def test_disconnect_is_recorded_for_operator_visibility():
    class DisconnectedBroker(FakeBroker):
        def get_all_positions(self):
            raise ConnectionError("paper broker unavailable")

    with pytest.raises(ConnectionError):
        engine.cycle(DisconnectedBroker(), submit_enabled=True)
    assert engine.load()["last_error"]
