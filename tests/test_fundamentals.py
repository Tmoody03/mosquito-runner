import fundamentals


def fact(value, end="2026-06-30"):
    return {"units": {"USD": [{"val": value, "end": end, "filed": "2026-08-01", "form": "10-Q"}]}}


def payload(*, assets=500, liabilities=300, equity=200, cash=60, current_debt=20,
            long_debt=80, backlog=100):
    facts = {
        "Assets": fact(assets), "Liabilities": fact(liabilities),
        "StockholdersEquity": fact(equity), "CashAndCashEquivalentsAtCarryingValue": fact(cash),
        "LongTermDebtCurrent": fact(current_debt), "LongTermDebtNoncurrent": fact(long_debt),
    }
    if backlog is not None:
        facts["RevenueRemainingPerformanceObligation"] = fact(backlog)
    return {"facts": {"us-gaap": facts}}


def inspect(data):
    return fundamentals.inspect_symbol("TEST", 1, fetch_json=lambda _url: data)


def test_positive_equity_asset_cushion_and_backlog_pass():
    result = inspect(payload())
    assert result["passed"] is True
    assert result["asset_coverage"] == 500 / 300
    assert result["backlog_confirmed"] is True


def test_cash_can_cover_debt_when_asset_ratio_is_below_cushion():
    result = inspect(payload(assets=500, liabilities=450, equity=50, cash=150))
    assert result["passed"] is True
    assert result["cash_covers_debt"] is True


def test_negative_balance_fails():
    result = inspect(payload(assets=300, liabilities=400, equity=-100, cash=200))
    assert result["passed"] is False
    assert "nonpositive_net_assets" in result["reasons"]
    assert "nonpositive_equity" in result["reasons"]


def test_uncovered_debt_fails():
    result = inspect(payload(assets=500, liabilities=450, equity=50, cash=10, long_debt=550))
    assert result["passed"] is False
    assert "debt_not_covered" in result["reasons"]


def test_missing_backlog_fails_closed(monkeypatch):
    monkeypatch.setattr(fundamentals, "_backlog_from_filing", lambda *_args: (False, None, "2026-08-01"))
    result = inspect(payload(backlog=None))
    assert result["passed"] is False
    assert "missing_positive_backlog_evidence" in result["reasons"]


def test_screen_only_returns_passes_from_fresh_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(fundamentals, "CACHE_PATH", tmp_path / "cache.json")
    checked = fundamentals.datetime.now(fundamentals.timezone.utc).isoformat()
    fundamentals.CACHE_PATH.write_text('{"GOOD":{"passed":true,"checked_at":"%s","asset_coverage":2,"backlog_confirmed":true},"BAD":{"passed":false,"checked_at":"%s"}}' % (checked, checked))
    rows = [{"ticker": "GOOD", "score": 99}, {"ticker": "BAD", "score": 98}]
    result = fundamentals.screen_ranked(rows, required=50)
    assert [row["ticker"] for row in result] == ["GOOD"]
    assert result[0]["fundamental_gate"] == "PASS"


def test_filing_statement_fallback_preserves_hard_rules(monkeypatch):
    import pandas as pd
    statement = pd.DataFrame({pd.Timestamp("2026-06-30"): {
        "Total Assets": 500, "Total Liabilities Net Minority Interest": 300,
        "Stockholders Equity": 200, "Cash Cash Equivalents And Short Term Investments": 150,
        "Total Debt": 100, "Current Deferred Revenue": 80,
    }})
    class Ticker:
        quarterly_balance_sheet = statement
    monkeypatch.setattr("yfinance.Ticker", lambda _symbol: Ticker())
    result = fundamentals.inspect_symbol("TEST", None)
    assert result["passed"] is True
    assert result["evidence_provider"] == "filing-derived-yfinance"
    assert result["backlog_value"] == 80
