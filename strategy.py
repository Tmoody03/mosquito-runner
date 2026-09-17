from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

# Broad exposure themes, not a claim that every company is an AI pure-play.
AI_UNIVERSE_BY_CATEGORY = {
    "compute_semiconductors": """AMD ARM ASML AVGO INTC MRVL MU NVDA NXPI ON QCOM TSM TXN ADI MCHP MPWR SWKS QRVO LSCC ALGM MTSI SITM CRUS POWI ACLS AMBA RMBS WOLF STM UMC HIMX GFS COHU FORM INDI DIOD SYNA SLAB SMTC IPGP PI AEHR NVMI CAMT""".split(),
    "semiconductor_equipment_design": """AMAT KLAC LRCX TER ENTG MKSI ICHR AOSL COHR PLAB VECO ASYS KLIC UCTT CDNS SNPS ANSS KEYS ADSK PTC ALTR FN JBL FLEX SANM CLS BHE TTMI""".split(),
    "cloud_data_software": """AAPL AMZN GOOG GOOGL META MSFT ORCL IBM CRM NOW ADBE INTU SAP UBER ABNB PLTR SNOW DDOG MDB NET ESTC CFLT PATH AI BBAI SOUN TEM APP GTLB TEAM HUBS TWLO DOCU ZM OKTA IOT PCOR DUOL KVYO S FROG NCNO BOX DBX DOCN WDAY ROP""".split(),
    "networking_storage_datacenters": """ANET CSCO HPE DELL SMCI VRT EQIX DLR AMT CCI GLW LITE CIEN INFN CALX NTAP PSTG WDC STX CRDO AAOI COMM UI RBBN VIAV JABIL APH TEL MSI FFIV AKAM ZBRA IRM DBRG COR AMKR WTS NBIS""".split(),
    "security_data_observability": """PANW CRWD FTNT ZS CYBR CHKP GEN TENB VRNS RPD QLYS SAIL CACI LDOS SAIC DT INFA OS PNFP""".split(),
    "power_cooling_industrial_robotics": """GEV NEE CEG VST ETN PWR HUBB EMR ROK HON GE PH CAT DE ABB FANUY YASKY DY IR AME ITT CARR JCI TT FIX GWW FAST URI GNRC BEPC CWEN AES EIX DUK SO EXC D NRG LEU CCJ BWXT SMR OKLO NVT MOD LIN APD ECL FSLR""".split(),
    "autonomy_mobility_space": """TSLA MBLY AUR LAZR INVZ OUST JOBY ACHR LUNR RKLB AVAV KTOS TDY NOC LMT RTX BA TXT GD HII HEI SPR GRMN TRMB PCAR CMI GM F RIVN LI XPEV NIO""".split(),
    "medical_healthcare_ai": """ABT ABBV ALNY AMGN BDX BMY BSX CAH CI CNC CVS DXCM EW GILD HCA HOLX HUM IDXX ILMN INCY ISRG JNJ LH LLY MDT MRNA MRK NTRA PFE REGN RMD RPRX SYK TMO UNH VEEV VRTX WAT ZBH ZTS GH EXAS PACB RXRX SDGR CERT DOCS HIMS OSCR TDOC DHR IQV TECH CRL A WST COO PODD MASI PEN ALGN STE GEHC PHG NVS AZN SNY TAK BGNE BMRN BIIB NBIX IONS QDEL DGX ELV MOH HQY RGEN RVTY OPCH""".split(),
}
AI_UNIVERSE = sorted({ticker for tickers in AI_UNIVERSE_BY_CATEGORY.values() for ticker in tickers})
MIN_HISTORY_DAYS = 127
DOWNLOAD_BATCH_SIZE = 80
REVERSAL_WEIGHT = 0.10
REVERSAL_LOOKBACK = 126


def _feature_frame(close, volume):
    latest = close.iloc[-1]
    returns = {n: latest / close.iloc[-n] - 1 for n in (5, 22, 66, 126)}
    daily = close.pct_change(fill_method=None).tail(126)
    raw = pd.DataFrame({
        "momentum_1w": returns[5], "momentum_1m": returns[22],
        "momentum_3m": returns[66], "momentum_6m": returns[126],
        "breakout": latest / close.tail(126).max() - 1,
        "relative_volume": volume.tail(22).mean() / volume.tail(66).mean() - 1,
        "quality": daily.mean() / daily.std(),
    })
    return raw.replace([np.inf, -np.inf], np.nan).dropna()


def _rank01(series):
    return series.rank(pct=True).fillna(0.5)


def _extract_field(data, field, requested):
    if data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        if field in data.columns.get_level_values(0):
            result = data[field]
        elif field in data.columns.get_level_values(-1):
            result = data.xs(field, axis=1, level=-1)
        else:
            return pd.DataFrame(index=data.index)
    elif field in data.columns and len(requested) == 1:
        result = data[[field]].rename(columns={field: requested[0]})
    else:
        return pd.DataFrame(index=data.index)
    if isinstance(result, pd.Series):
        result = result.to_frame(name=requested[0])
    result.columns = [str(column).upper() for column in result.columns]
    return result.loc[:, ~result.columns.duplicated()]


def _download_universe(tickers):
    opens, closes, volumes = [], [], []
    for start in range(0, len(tickers), DOWNLOAD_BATCH_SIZE):
        batch = tickers[start:start + DOWNLOAD_BATCH_SIZE]
        try:
            # Fetch extra calendar history so 127 completed sessions remain
            # available after weekends, holidays, and exclusion of today's bar.
            # The actual return metric remains exactly 126 trading sessions.
            data = yf.download(batch, period="8mo", interval="1d", auto_adjust=True,
                               progress=False, group_by="column", threads=True)
        except Exception:
            continue
        close = _extract_field(data, "Close", batch)
        opened = _extract_field(data, "Open", batch)
        volume = _extract_field(data, "Volume", batch)
        common = close.columns.intersection(volume.columns)
        if len(common):
            closes.append(close[common]); volumes.append(volume[common])
            opens.append(opened[common.intersection(opened.columns)])
    if not closes:
        raise RuntimeError("Market data is temporarily unavailable")
    close = pd.concat(closes, axis=1).sort_index()
    volume = pd.concat(volumes, axis=1).reindex(close.index)
    opened = pd.concat(opens, axis=1).reindex(close.index) if opens else pd.DataFrame(index=close.index)
    return (close.loc[:, ~close.columns.duplicated()], volume.loc[:, ~volume.columns.duplicated()],
            opened.loc[:, ~opened.columns.duplicated()])


def _category_for(ticker):
    return next((category for category, tickers in AI_UNIVERSE_BY_CATEGORY.items()
                 if ticker in tickers), "other")


def _reversal_frame(opened, close):
    """Return the auditable prior-session reversal equation for each symbol."""
    result = pd.DataFrame(index=close.columns, data={
        "reversal_six_month_return": np.nan, "reversal_five_day_return": np.nan,
        "reversal_prior_session_return": np.nan, "reversal_prior_day_gap": np.nan,
        "reversal_signal": False,
    })
    if opened is None or close.shape[0] < REVERSAL_LOOKBACK + 1:
        return result
    opened = opened.reindex(index=close.index, columns=close.columns)
    latest, prior = close.iloc[-1], close.iloc[-2]
    result["reversal_six_month_return"] = latest / close.iloc[-1-REVERSAL_LOOKBACK] - 1
    result["reversal_five_day_return"] = latest / close.iloc[-6] - 1
    result["reversal_prior_session_return"] = latest / prior - 1
    result["reversal_prior_day_gap"] = opened.iloc[-1] / prior - 1
    finite = np.isfinite(result.iloc[:, :4]).all(axis=1)
    result["reversal_signal"] = (finite &
        (result.reversal_six_month_return < 0) &
        (result.reversal_five_day_return > 0) &
        (result.reversal_prior_session_return > 0) &
        (result.reversal_prior_day_gap > 0))
    return result


def rank_watchlist(close,volume,count=50,*,opened=None,trade_date=None,source="provided_market_data"):
    count = max(1, min(int(count), len(AI_UNIVERSE)))
    close=close.sort_index();volume=volume.reindex(close.index)
    if trade_date is None:
        trade_date=datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    cutoff=pd.Timestamp(trade_date)
    if close.index.tz is not None:
        cutoff=cutoff.tz_localize(close.index.tz) if cutoff.tzinfo is None else cutoff.tz_convert(close.index.tz)
    elif cutoff.tzinfo is not None:
        cutoff=cutoff.tz_localize(None)
    # Never rank on the proposed trade session's partial daily bar.
    mask=close.index<cutoff
    close=close.loc[mask];volume=volume.loc[mask]
    if opened is not None:opened=opened.reindex(close.index)
    eligible = [ticker for ticker in close.columns
                if close[ticker].dropna().shape[0] >= MIN_HISTORY_DAYS]
    if not eligible:
        raise RuntimeError("No stocks met the six-month price-history requirement")
    clean_close = close[eligible].ffill()  # no backfill from future observations
    latest = clean_close.iloc[-1]
    frame = _feature_frame(clean_close, volume[eligible].fillna(0))
    if frame.empty:
        raise RuntimeError("Insufficient valid market history to rank stocks")
    frame["base_score"] = (0.10*_rank01(frame.momentum_1w)+0.20*_rank01(frame.momentum_1m)+
                            0.22*_rank01(frame.momentum_3m)+0.25*_rank01(frame.momentum_6m)+
                            0.10*_rank01(frame.breakout)+0.08*_rank01(frame.relative_volume)+
                            0.05*_rank01(frame.quality))
    reversal=_reversal_frame(opened.reindex(columns=eligible) if opened is not None else None,clean_close).reindex(frame.index)
    frame=frame.join(reversal)
    # Preserve breadth: this is a soft overlay, never an eligibility gate. Among
    # complete four-condition matches, a six-month loss closer to zero receives
    # the higher percentile. Nonmatches receive zero.
    frame["reversal_overlay_score"] = 0.0
    matched=frame.reversal_signal.fillna(False)
    if matched.any():
        frame.loc[matched,"reversal_overlay_score"] = frame.loc[matched,"reversal_six_month_return"].rank(pct=True)
    frame["reversal_score_contribution"] = frame.reversal_overlay_score*REVERSAL_WEIGHT
    frame["teddy_score"] = (1-REVERSAL_WEIGHT)*frame.base_score+frame.reversal_score_contribution
    frame["ticker_sort"] = frame.index
    picks = frame.sort_values(["teddy_score","ticker_sort"], ascending=[False,True]).head(count)
    positive_signal = float(frame.momentum_1m.median()) > 0
    april_gate = datetime.now(timezone.utc).month != 4 or positive_signal
    rows = [{"rank": i+1, "ticker": ticker, "category": _category_for(ticker),
             "score": round(float(row.teddy_score)*100, 2),
             "base_score": round(float(row.base_score)*100, 4),
             "reversal_match": bool(row.reversal_signal),
             "reversal_overlay_score": round(float(row.reversal_overlay_score)*100, 4),
             "reversal_score_contribution": round(float(row.reversal_score_contribution)*100, 4),
             "reversal_metrics": {name.removeprefix("reversal_"): (round(float(row[name]), 10) if pd.notna(row[name]) else None)
                                  for name in ("reversal_six_month_return","reversal_five_day_return","reversal_prior_session_return","reversal_prior_day_gap")},
             "weight_pct": round(100/len(picks), 6),
             "price": round(float(latest[ticker]), 6)}
            for i, (ticker, row) in enumerate(picks.iterrows())]
    return {"strategy": "V5.8 Master", "count": len(rows), "picks": rows,
            "eligible_count": len(frame), "universe_count": len(AI_UNIVERSE),
            "data_source":source,
            "data_through": close.index[-1].date().isoformat(), "trade_date": str(pd.Timestamp(trade_date).date()),
            "reversal_weight_pct": REVERSAL_WEIGHT*100,
            "ranking_equation": "0.90 * V5.8 base score + 0.10 * reversal overlay percentile",
            "reversal_rule": "negative 126-session return + positive 5-session return + positive prior session + positive prior-day gap",
            "positive_signal": positive_signal, "april_trade_allowed": april_gate,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "notice": "Broad AI, infrastructure, and medical-AI exposure themes—not pure-play claims. Alpaca paper trading only."}


def build_watchlist(count=50):
    close,volume,opened=_download_universe(AI_UNIVERSE)
    return rank_watchlist(close,volume,count,opened=opened,source="yfinance_research_fallback")
