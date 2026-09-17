import numpy as np
import pandas as pd

import strategy


def _market_frame(tickers, periods=130):
    dates = pd.bdate_range("2021-01-01", periods=periods)
    columns = pd.MultiIndex.from_product([["Close", "Open", "Volume"], tickers])
    values = np.empty((periods, len(columns)))
    for index, (field, _ticker) in enumerate(columns):
        values[:, index] = (np.linspace(50, 100 + index, periods) if field in {"Close", "Open"}
                            else np.full(periods, 1_000_000 + index))
    return pd.DataFrame(values, index=dates, columns=columns)


def test_universe_is_large_unique_and_categorized():
    assert len(strategy.AI_UNIVERSE) >= 220
    assert len(strategy.AI_UNIVERSE) == len(set(strategy.AI_UNIVERSE))
    assert len(strategy.AI_UNIVERSE_BY_CATEGORY["medical_healthcare_ai"]) >= 50


def test_watchlist_returns_fifty_equal_weighted_prior_data_picks(monkeypatch):
    tickers = strategy.AI_UNIVERSE[:65]
    frame = _market_frame(tickers)
    monkeypatch.setattr(strategy, "DOWNLOAD_BATCH_SIZE", 1000)
    monkeypatch.setattr(strategy.yf, "download", lambda *args, **kwargs: frame)
    result = strategy.build_watchlist(50)
    assert result["count"] == 50
    assert result["eligible_count"] == 65
    assert result["universe_count"] >= 220
    assert all(row["weight_pct"] == 2.0 for row in result["picks"])
    assert all(row["category"] != "other" for row in result["picks"])


def test_six_month_filter_excludes_short_history(monkeypatch):
    tickers = strategy.AI_UNIVERSE[:3]
    frame = _market_frame(tickers)
    frame.loc[frame.index[:25], ("Close", tickers[0])] = np.nan
    monkeypatch.setattr(strategy, "DOWNLOAD_BATCH_SIZE", 1000)
    monkeypatch.setattr(strategy.yf, "download", lambda *args, **kwargs: frame)
    result = strategy.build_watchlist(3)
    assert result["count"] == 2
    assert tickers[0] not in {row["ticker"] for row in result["picks"]}


def test_download_survives_failed_batch(monkeypatch):
    monkeypatch.setattr(strategy, "DOWNLOAD_BATCH_SIZE", 2)
    calls = {"count": 0}

    def download(batch, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("one provider batch failed")
        return _market_frame(batch)

    monkeypatch.setattr(strategy.yf, "download", download)
    close, volume, opened = strategy._download_universe(strategy.AI_UNIVERSE[:6])
    assert len(close.columns) == 4
    assert list(close.columns) == list(volume.columns) == list(opened.columns)


def test_reversal_equation_contributes_auditable_soft_overlay():
    tickers = strategy.AI_UNIVERSE[:2]
    dates = pd.bdate_range("2025-01-02", periods=130)
    volume = pd.DataFrame(1_000_000.0, index=dates, columns=tickers)
    close = pd.DataFrame(index=dates, columns=tickers, dtype=float)
    opened = pd.DataFrame(index=dates, columns=tickers, dtype=float)
    for index,ticker in enumerate(tickers):
        values = np.linspace(100, 80+index*5, len(dates));values[-6:] = np.linspace(76+index*5,81+index*5,6)
        close[ticker]=values;opened[ticker]=values;opened.loc[dates[-1],ticker]=values[-2]*1.001
    result=strategy.rank_watchlist(close,volume,2,opened=opened,trade_date="2026-01-01")
    assert all(row["reversal_match"] for row in result["picks"])
    assert sorted(row["reversal_overlay_score"] for row in result["picks"]) == [50.0,100.0]
    assert sorted(row["reversal_score_contribution"] for row in result["picks"]) == [5.0,10.0]
    assert result["reversal_weight_pct"] == 10.0
    assert result["ranking_equation"].startswith("0.90")
    assert result["data_through"] == dates[-1].date().isoformat()


def test_nonmatching_stock_keeps_base_ranking_with_zero_overlay():
    tickers = strategy.AI_UNIVERSE[:2]
    frame = _market_frame(tickers)
    close,volume,opened=(frame[field] for field in ("Close","Volume","Open"))
    result=strategy.rank_watchlist(close,volume,2,opened=opened,trade_date="2026-01-01")
    assert all(not row["reversal_match"] for row in result["picks"])
    assert all(row["reversal_overlay_score"] == 0 for row in result["picks"])
    assert all(row["score"] == round(row["base_score"]*.9,2) for row in result["picks"])


def test_ranker_excludes_trade_day_bar_and_supports_legacy_callers():
    tickers = strategy.AI_UNIVERSE[:2]
    frame = _market_frame(tickers, periods=131)
    close,volume,opened=(frame[field] for field in ("Close","Volume","Open"))
    trade_date=close.index[-1].date().isoformat()
    baseline=strategy.rank_watchlist(close.iloc[:-1],volume.iloc[:-1],2,
        opened=opened.iloc[:-1],trade_date=trade_date)
    poisoned_close=close.copy();poisoned_open=opened.copy();poisoned_volume=volume.copy()
    poisoned_close.iloc[-1]=99999;poisoned_open.iloc[-1]=99999;poisoned_volume.iloc[-1]=99999
    guarded=strategy.rank_watchlist(poisoned_close,poisoned_volume,2,
        opened=poisoned_open,trade_date=trade_date)
    assert guarded["picks"] == baseline["picks"]
    # Existing callers that do not provide opens still get the V5.8 ranking;
    # the optional overlay simply fails closed to zero.
    legacy=strategy.rank_watchlist(close.iloc[:-1],volume.iloc[:-1],2,trade_date=trade_date)
    assert legacy["count"] == 2
    assert all(row["reversal_overlay_score"] == 0 for row in legacy["picks"])
