"""Pure price-protection rules shared by broker and simulation code."""
from __future__ import annotations

import math

TRAILING_DROP = 0.0005


def money_tick_up(value: float) -> float:
    """Round a sell floor upward so it can never weaken the protection."""
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("price must be a positive finite number")
    return math.ceil((value - 1e-12) * 100) / 100


def protected_state(entry: float, current: float, previous_peak: float | None = None) -> dict:
    """Evaluate the 0.05% trail and the immediate below-entry exit rule."""
    entry, current = float(entry), float(current)
    values = (entry, current)
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("entry and current must be positive finite numbers")
    prior = entry if previous_peak is None else float(previous_peak)
    if not math.isfinite(prior) or prior <= 0:
        prior = entry
    peak = max(entry, current, prior)
    trigger = peak * (1 - TRAILING_DROP)
    trail_armed = peak > entry
    below_entry = current + 1e-9 < entry
    trailing_exit = trail_armed and current <= trigger + 1e-9
    should_sell = below_entry or trailing_exit
    sell_reason = "BELOW_ENTRY_EXIT" if below_entry else "TRAIL_0.05_PERCENT" if trailing_exit else None
    return {
        "entry_price": entry,
        "current_price": current,
        "peak_price": peak,
        "trailing_trigger": trigger,
        "protected_floor": money_tick_up(entry),
        "trail_armed": trail_armed,
        "below_entry": below_entry,
        "sell_reason": sell_reason,
        "should_sell": should_sell,
        "status": "EXIT_BELOW_ENTRY" if below_entry else "SELL_SIGNAL" if should_sell else "TRAILING" if trail_armed else "WAITING_FOR_GAIN",
    }
