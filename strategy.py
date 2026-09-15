from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

# Public, non-OTC AI infrastructure universe. The four-year data rule is enforced below.
AI_UNIVERSE = sorted(set("""
AAPL AMD AMAT ANET ASML AVGO CDNS CLS COHR CRDO CRM CSCO DELL EQIX FSLR GEV GLW
GOOG GOOGL HPE IBM INTC LITE LRCX META MRVL MSFT MU NBIS NEE NFLX NOW NVDA NXPI
ORCL PANW PLTR QCOM ROK ROP SMCI SNPS TSM VRT WDC WDAY ZS
""".split()))


def _feature_frame(close, volume):
    latest = close.iloc[-1]
    returns = {n: latest / close.iloc[-n] - 1 for n in (22, 66, 132, 252)}
    breakout = latest / close.tail(252).max() - 1
    rel_volume = volume.tail(22).mean() / volume.tail(66).mean() - 1
    quality = close.pct_change().tail(252).mean() / close.pct_change().tail(252).std()
    raw = pd.DataFrame({"momentum_1m": returns[22], "momentum_3m": returns[66],
                        "momentum_6m": returns[132], "momentum_12m": returns[252],
                        "breakout": breakout, "relative_volume": rel_volume, "quality": quality})
    return raw.replace([np.inf, -np.inf], np.nan).dropna()


def _rank01(s):
    return s.rank(pct=True).fillna(0.5)


def build_watchlist(count=50):
    data = yf.download(AI_UNIVERSE, period="5y", interval="1d", auto_adjust=True,
                       progress=False, group_by="column", threads=True)
    if data.empty:
        raise RuntimeError("Market data is temporarily unavailable")
    close, volume = data["Close"], data["Volume"]
    eligible = [t for t in close.columns if close[t].dropna().shape[0] >= 1008]
    frame = _feature_frame(close[eligible].ffill(), volume[eligible].fillna(0))
    # Reconstructed, explicit V5.8 feature blend. No future holding-period data is used.
    frame["teddy_score"] = (0.10*_rank01(frame.momentum_1m)+0.15*_rank01(frame.momentum_3m)+
                            0.25*_rank01(frame.momentum_6m)+0.20*_rank01(frame.momentum_12m)+
                            0.12*_rank01(frame.breakout)+0.08*_rank01(frame.relative_volume)+
                            0.10*_rank01(frame.quality))
    picks = frame.sort_values("teddy_score", ascending=False).head(count)
    positive_signal = float(frame.momentum_1m.median()) > 0
    april_gate = datetime.now(timezone.utc).month != 4 or positive_signal
    rows = [{"rank": i+1, "ticker": ticker, "score": round(float(row.teddy_score)*100, 2),
             "weight_pct": round(100/len(picks), 2)} for i, (ticker, row) in enumerate(picks.iterrows())]
    return {"strategy": "V5.8 Master", "count": len(rows), "picks": rows,
            "positive_signal": positive_signal, "april_trade_allowed": april_gate,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "notice": "Research and Alpaca paper-preview only. Verify Fidelity tradability before orders."}
