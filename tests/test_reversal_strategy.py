import numpy as np
import pandas as pd
import pytest

import reversal_strategy


def _prices():
    dates = pd.bdate_range("2025-01-02", periods=128, tz="UTC")
    closes, opens = {}, {}
    # Both qualify. BBB has the six-month loss closest to zero and ranks first.
    for symbol, finish in (("AAA", 85.0), ("BBB", 95.0)):
        values = np.linspace(100.0, finish - 6, len(dates))
        values[-6:] = np.linspace(finish - 5, finish, 6)
        closes[symbol] = values
        gap_opens = values.copy()
        gap_opens[-1] = values[-2] * 1.001
        opens[symbol] = gap_opens
    # CCC keeps a negative five-day return and must fail.
    values = np.linspace(100.0, 70.0, len(dates))
    values[-2], values[-1] = 69.0, 69.5
    closes["CCC"] = values
    opens["CCC"] = values.copy(); opens["CCC"][-1] = values[-2] * 1.001
    return pd.DataFrame(opens, index=dates), pd.DataFrame(closes, index=dates)


def test_shadow_rules_ranking_cap_and_audit_metadata():
    opens, closes = _prices()
    result = reversal_strategy.build_shadow_watchlist(
        opens, closes, trade_date="2025-07-02", count=99, estimated_round_trip_bps=12.5)
    assert [row["ticker"] for row in result["picks"]] == ["BBB", "AAA"]
    assert all(all(row["signals"].values()) for row in result["picks"])
    assert all(row["weight_pct"] == 50.0 for row in result["picks"])
    assert result["position_cap"] == 25 and result["order_submission"] is False
    assert result["mode"] == "research_shadow_only"
    assert result["estimated_round_trip_cost_bps"] == 12.5
    assert "not guarantee" in result["profit_warning"]


def test_trade_day_values_are_ignored_to_prevent_lookahead():
    opens, closes = _prices()
    trade_date = closes.index[-1].date().isoformat()
    baseline = reversal_strategy.build_shadow_watchlist(
        opens.iloc[:-1], closes.iloc[:-1], trade_date=trade_date)
    poisoned_open, poisoned_close = opens.copy(), closes.copy()
    poisoned_open.iloc[-1] = 9999
    poisoned_close.iloc[-1] = 9999
    guarded = reversal_strategy.build_shadow_watchlist(
        poisoned_open, poisoned_close, trade_date=trade_date)
    assert guarded["picks"] == baseline["picks"]
    assert guarded["data_through"] < trade_date


def test_requires_enough_completed_history():
    opens, closes = _prices()
    with pytest.raises(ValueError, match="127 completed sessions"):
        reversal_strategy.build_shadow_watchlist(
            opens.iloc[:100], closes.iloc[:100], trade_date="2026-01-01")


def test_selection_is_hard_capped_at_twenty_five():
    opens, closes = _prices()
    base_open, base_close = opens["BBB"], closes["BBB"]
    many_open = pd.concat({f"S{index:02d}": base_open for index in range(30)}, axis=1)
    many_close = pd.concat({f"S{index:02d}": base_close for index in range(30)}, axis=1)
    result = reversal_strategy.build_shadow_watchlist(
        many_open, many_close, trade_date="2025-07-02", count=100)
    assert result["count"] == 25
    assert [row["ticker"] for row in result["picks"]] == [f"S{index:02d}" for index in range(25)]
