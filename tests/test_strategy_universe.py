import numpy as np
import pandas as pd

import strategy


def _market_frame(tickers, periods=130):
    dates = pd.bdate_range("2021-01-01", periods=periods)
    columns = pd.MultiIndex.from_product([["Close", "Volume"], tickers])
    values = np.empty((periods, len(columns)))
    for index, (field, _ticker) in enumerate(columns):
        values[:, index] = (np.linspace(50, 100 + index, periods) if field == "Close"
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
    close, volume = strategy._download_universe(strategy.AI_UNIVERSE[:6])
    assert len(close.columns) == 4
    assert list(close.columns) == list(volume.columns)
