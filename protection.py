"""Pure price-protection rules shared by broker and simulation code."""
from __future__ import annotations

import math

TRAILING_DROP = 0.0005
PROFIT_ARM_GAIN = 0.001


def money_tick_up(value: float) -> float:
    """Round a sell floor upward so it can never weaken the protection."""
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("price must be a positive finite number")
    return math.ceil((value - 1e-12) * 100) / 100


def protected_state(entry: float, current: float, previous_peak: float | None = None) -> dict:
    """Evaluate Mosquito's green-ribbon profit lock.

    A new fill is deliberately *not* sold merely because its first mark is below
    the fill price: that is normally the bid/ask spread.  The trail arms only
    after an executable price has advanced 0.10% from the actual fill.  Once
    armed, a 0.05% retreat from the high-water mark signals a protected limit
    exit.  The limit floor is never below the fill price.
    """
    entry, current = float(entry), float(current)
    values = (entry, current)
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("entry and current must be positive finite numbers")
    prior = entry if previous_peak is None else float(previous_peak)
    if not math.isfinite(prior) or prior <= 0:
        prior = entry
    peak = max(entry, current, prior)
    arm_price = entry * (1 + PROFIT_ARM_GAIN)
    trail_armed = peak + 1e-9 >= arm_price
    raw_trigger = peak * (1 - TRAILING_DROP)
    trigger = max(entry, raw_trigger)
    below_entry = current + 1e-9 < entry
    trailing_exit = trail_armed and current <= trigger + 1e-9
    should_sell = trailing_exit and not below_entry
    sell_reason = "GREEN_RIBBON_TRAIL_0.05_PERCENT" if should_sell else None
    return {
        "entry_price": entry,
        "current_price": current,
        "peak_price": peak,
        "profit_arm_price": arm_price,
        "trailing_trigger": trigger,
        # A sell limit at the fill-price floor may execute at a better price,
        # but it will not intentionally cross below the recorded purchase.
        "protected_floor": money_tick_up(entry),
        "trail_armed": trail_armed,
        "below_entry": below_entry,
        "sell_reason": sell_reason,
        "should_sell": should_sell,
        "status": "PROTECTED_BELOW_ENTRY" if below_entry else "SELL_SIGNAL" if should_sell else "TRAILING" if trail_armed else "WAITING_FOR_GAIN",
    }
