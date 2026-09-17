"""Pure, non-executable research screen for the Mosquito reversal hypothesis.

This module deliberately has no broker imports and cannot submit orders.  Every
feature is calculated from completed sessions strictly before ``trade_date`` so
the result can be reproduced without using information from the trade session.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd


STRATEGY_NAME = "Prior-Day Reversal Shadow"
MAX_POSITIONS = 25
SIX_MONTH_SESSIONS = 126
FIVE_DAY_SESSIONS = 5


def _frame(value, label):
    frame = value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
    if frame.empty:
        raise ValueError(f"{label} prices are required")
    try:
        frame.index = pd.to_datetime(frame.index, utc=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} index must contain valid session dates") from exc
    frame.columns = [str(column).upper().strip() for column in frame.columns]
    if not all(frame.columns) or frame.columns.duplicated().any():
        raise ValueError(f"{label} symbols must be nonempty and unique")
    return frame.apply(pd.to_numeric, errors="coerce").sort_index()


def build_shadow_watchlist(open_prices, close_prices, *, trade_date, count=MAX_POSITIONS,
                           estimated_round_trip_bps=10.0):
    """Return a deterministic, equal-weight research watchlist.

    Rules use only completed sessions before ``trade_date``: negative 126-session
    return, positive five-session return, positive prior-session return, and a
    positive gap on that prior session. Qualifiers are ranked by six-month loss
    closest to zero, then ticker, and capped at 25.
    """
    opens, closes = _frame(open_prices, "open"), _frame(close_prices, "close")
    try:
        cutoff = pd.Timestamp(trade_date)
        cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    except (TypeError, ValueError) as exc:
        raise ValueError("trade_date must be a valid date") from exc
    try:
        requested = int(count)
        cost_bps = float(estimated_round_trip_bps)
    except (TypeError, ValueError) as exc:
        raise ValueError("count and estimated_round_trip_bps must be numeric") from exc
    if requested < 1:
        raise ValueError("count must be positive")
    if not np.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("estimated_round_trip_bps must be finite and nonnegative")

    common_sessions = opens.index.intersection(closes.index)
    symbols = sorted(set(opens.columns).intersection(closes.columns))
    if not symbols:
        raise ValueError("open and close prices have no common symbols")
    opens = opens.loc[common_sessions, symbols]
    closes = closes.loc[common_sessions, symbols]
    # The strict comparison is the lookahead barrier: trade-day open/close values
    # are ignored even if a caller accidentally includes them.
    opens, closes = opens.loc[opens.index < cutoff], closes.loc[closes.index < cutoff]
    if len(closes.index) < SIX_MONTH_SESSIONS + 1:
        raise ValueError("at least 127 completed sessions before trade_date are required")

    evaluated = []
    for symbol in symbols:
        history = pd.DataFrame({"open": opens[symbol], "close": closes[symbol]}).dropna()
        if len(history) < SIX_MONTH_SESSIONS + 1:
            continue
        history = history.iloc[-(SIX_MONTH_SESSIONS + 1):]
        values = history[["open", "close"]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            continue
        latest = float(history.close.iloc[-1])
        prior_close = float(history.close.iloc[-2])
        returns = {
            "six_month_return": latest / float(history.close.iloc[-1-SIX_MONTH_SESSIONS]) - 1,
            "five_day_return": latest / float(history.close.iloc[-1-FIVE_DAY_SESSIONS]) - 1,
            "prior_session_return": latest / prior_close - 1,
            "prior_day_gap": float(history.open.iloc[-1]) / prior_close - 1,
        }
        signals = {
            "negative_six_month": returns["six_month_return"] < 0,
            "positive_five_day": returns["five_day_return"] > 0,
            "positive_prior_session": returns["prior_session_return"] > 0,
            "positive_prior_day_gap": returns["prior_day_gap"] > 0,
        }
        evaluated.append({
            "ticker": symbol,
            **{key: round(value, 10) for key, value in returns.items()},
            "signals": signals,
            "qualified": all(signals.values()),
            "history_start": history.index[0].date().isoformat(),
            "signal_session": history.index[-1].date().isoformat(),
        })

    qualified = sorted(
        (row for row in evaluated if row["qualified"]),
        key=lambda row: (-row["six_month_return"], row["ticker"]),
    )
    selected = qualified[:min(requested, MAX_POSITIONS)]
    weight = 100.0 / len(selected) if selected else 0.0
    picks = [{**row, "rank": rank, "weight_pct": round(weight, 8)}
             for rank, row in enumerate(selected, 1)]
    return {
        "strategy": STRATEGY_NAME,
        "mode": "research_shadow_only",
        "paper_only": True,
        "order_submission": False,
        "trade_date": cutoff.date().isoformat(),
        "data_through": closes.index[-1].date().isoformat(),
        "lookahead_guard": "all inputs on or after trade_date ignored",
        "ranking": "six_month_loss_closest_to_zero_then_ticker",
        "requested_count": requested,
        "position_cap": MAX_POSITIONS,
        "count": len(picks),
        "evaluated_count": len(evaluated),
        "qualified_count": len(qualified),
        "estimated_round_trip_cost_bps": cost_bps,
        "cost_warning": "Trading costs, spread, slippage, taxes, and fill uncertainty are not deducted; they can eliminate an apparent edge.",
        "profit_warning": "A research signal and historical result do not guarantee profit.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "picks": picks,
    }
