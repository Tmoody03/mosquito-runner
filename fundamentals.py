"""Fail-closed SEC fundamentals gate for Teddy's eligible stock universe."""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SEC_BASE = "https://data.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT = os.getenv("MOSQUITO_SEC_USER_AGENT", "MosquitoPaperResearch/1.0 github.com/Tmoody03/mosquito-runner")
CACHE_PATH = Path(os.getenv("MOSQUITO_FUNDAMENTALS_FILE", "/data/mosquito-fundamentals.json"))
CACHE_DAYS = max(1, int(os.getenv("MOSQUITO_FUNDAMENTALS_CACHE_DAYS", "7")))
MIN_ASSET_COVERAGE = float(os.getenv("MOSQUITO_MIN_ASSET_COVERAGE", "1.20"))
LOCK = threading.RLock()
REQUEST_LOCK = threading.Lock()
LAST_REQUEST = 0.0

ASSET_TAGS = ("Assets",)
LIABILITY_TAGS = ("Liabilities",)
EQUITY_TAGS = ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity")
CASH_TAGS = ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalentsAtCarryingValue")
DEBT_TAG_GROUPS = (
    ("LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent"),
    ("LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebtNoncurrent"),
    ("ShortTermBorrowings",),
)
BACKLOG_TAGS = ("RevenueRemainingPerformanceObligation", "ContractWithCustomerLiability", "ContractWithCustomerLiabilityCurrent", "ContractWithCustomerLiabilityNoncurrent")
ALLOWED_FORMS = {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A", "20-F/A", "40-F/A"}


def _get_json(url):
    return json.loads(_get(url).decode("utf-8"))


def _get(url):
    global LAST_REQUEST
    # Stay below the SEC's published fair-access ceiling even across workers.
    with REQUEST_LOCK:
        wait = 0.12 - (time.monotonic() - LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        LAST_REQUEST = time.monotonic()
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urlopen(request, timeout=15) as response:
        return response.read()


def _load_cache():
    try:
        value = json.loads(CACHE_PATH.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".fundamentals-", dir=str(CACHE_PATH.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(cache, handle, separators=(",", ":"))
        os.replace(name, CACHE_PATH)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _fresh(row):
    try:
        checked = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - checked <= timedelta(days=CACHE_DAYS)
    except (KeyError, TypeError, ValueError):
        return False


def _latest(facts, tags):
    values = []
    for tag in tags:
        units = facts.get(tag, {}).get("units", {})
        for unit in ("USD", "shares"):
            for item in units.get(unit, []):
                if item.get("form") in ALLOWED_FORMS and isinstance(item.get("val"), (int, float)):
                    values.append((item.get("end", ""), item.get("filed", ""), float(item["val"]), tag))
    return max(values, default=(None, None, None, None))


def _total_debt(facts):
    values, tags = [], []
    for alternatives in DEBT_TAG_GROUPS:
        _end, _filed, value, tag = _latest(facts, alternatives)
        if value is not None:
            values.append(max(0.0, value)); tags.append(tag)
    return (sum(values), tags) if values else (None, [])


def _ticker_map():
    raw = _get_json(SEC_TICKERS)
    return {str(row["ticker"]).upper(): int(row["cik_str"]) for row in raw.values()}


def _backlog_from_filing(cik, submissions):
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    for index, form in enumerate(forms):
        if form not in ALLOWED_FORMS:
            continue
        try:
            accession = recent["accessionNumber"][index].replace("-", "")
            document = recent["primaryDocument"][index]
            filed = recent["filingDate"][index]
            raw = _get(f"{SEC_ARCHIVES}/{cik}/{accession}/{document}").decode("utf-8", "ignore")
        except (KeyError, IndexError, OSError, ValueError):
            continue
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text)
        # Require a positive disclosed quantity close to the backlog term.
        pattern = re.compile(r"(?i)(backlog|order book|remaining performance obligations?|contracted revenue).{0,240}?(?:\$\s*)?([0-9][0-9,.]*)\s*(billion|million|thousand)?")
        for match in pattern.finditer(text):
            value = float(match.group(2).replace(",", ""))
            if value > 0:
                return True, match.group(1).lower(), filed
        return False, None, filed
    return False, None, None


def inspect_symbol(symbol, cik, *, fetch_json=_get_json):
    """Return an auditable pass/fail result; any missing required fact fails."""
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        company = fetch_json(f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json")
        facts = company.get("facts", {}).get("us-gaap", {})
        _, _, assets, asset_tag = _latest(facts, ASSET_TAGS)
        _, _, liabilities, liability_tag = _latest(facts, LIABILITY_TAGS)
        _, _, equity, equity_tag = _latest(facts, EQUITY_TAGS)
        _, _, cash, cash_tag = _latest(facts, CASH_TAGS)
        debt, debt_tags = _total_debt(facts)
        _, backlog_filed, backlog_value, backlog_tag = _latest(facts, BACKLOG_TAGS)
        backlog_ok = backlog_value is not None and backlog_value > 0
        backlog_source = backlog_tag if backlog_ok else None
        if not backlog_ok:
            submissions = fetch_json(f"{SEC_BASE}/submissions/CIK{cik:010d}.json")
            backlog_ok, backlog_source, backlog_filed = _backlog_from_filing(cik, submissions)
        complete = all(value is not None for value in (assets, liabilities, equity, cash, debt))
        asset_coverage = assets / liabilities if complete and liabilities > 0 else None
        debt_covered = bool(complete and debt <= assets and (cash >= debt or asset_coverage >= MIN_ASSET_COVERAGE))
        passed = bool(complete and assets > liabilities and equity > 0 and debt_covered and backlog_ok)
        reasons = []
        if not complete: reasons.append("missing_balance_sheet_evidence")
        if complete and not assets > liabilities: reasons.append("nonpositive_net_assets")
        if complete and not equity > 0: reasons.append("nonpositive_equity")
        if complete and not debt_covered: reasons.append("debt_not_covered")
        if not backlog_ok: reasons.append("missing_positive_backlog_evidence")
        return {"ticker": symbol, "passed": passed, "checked_at": checked_at, "reasons": reasons,
                "assets": assets, "liabilities": liabilities, "equity": equity, "cash": cash,
                "debt": debt, "asset_coverage": asset_coverage, "cash_covers_debt": bool(complete and cash >= debt),
                "backlog_confirmed": backlog_ok, "backlog_value": backlog_value,
                "backlog_source": backlog_source, "backlog_filed": backlog_filed,
                "evidence_tags": [asset_tag, liability_tag, equity_tag, cash_tag, *debt_tags]}
    except Exception as exc:
        return {"ticker": symbol, "passed": False, "checked_at": checked_at,
                "reasons": ["sec_evidence_unavailable"], "error_type": type(exc).__name__,
                "backlog_confirmed": False}


def screen_ranked(rows, required=50):
    """Screen the complete ranked set and return only hard-gate passes."""
    cache = _load_cache()
    symbols = [str(row.get("ticker", "")).upper() for row in rows]
    missing = [symbol for symbol in symbols if not _fresh(cache.get(symbol, {}))]
    if missing:
        try:
            mapping = _ticker_map()
        except Exception:
            mapping = {}
        workers = min(4, max(1, len(missing)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(inspect_symbol, symbol, mapping[symbol]): symbol for symbol in missing if symbol in mapping}
            for future in concurrent.futures.as_completed(futures):
                cache[futures[future]] = future.result()
        for symbol in missing:
            if symbol not in mapping:
                cache[symbol] = {"ticker": symbol, "passed": False, "checked_at": datetime.now(timezone.utc).isoformat(),
                                 "reasons": ["sec_company_mapping_unavailable"], "backlog_confirmed": False}
        with LOCK:
            _save_cache(cache)
    passed = []
    for row in rows:
        result = cache.get(str(row.get("ticker", "")).upper(), {})
        if result.get("passed"):
            enriched = dict(row)
            enriched["fundamentals"] = {key: result.get(key) for key in
                ("asset_coverage", "cash_covers_debt", "backlog_confirmed", "backlog_value", "backlog_source", "backlog_filed", "checked_at")}
            enriched["fundamental_gate"] = "PASS"
            passed.append(enriched)
    return passed[:max(1, int(required))]
