import importlib
import math
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


class FakeClient:
    def __init__(self):
        self.closed = 0
    def get_account(self):
        return SimpleNamespace(id="acct", status="ACTIVE", currency="USD", cash="8000",
            portfolio_value="10000", equity="10000", last_equity="9900", buying_power="12000",
            daytrading_buying_power="12000", regt_buying_power="12000", trading_blocked=False,
            transfers_blocked=False, account_blocked=False, pattern_day_trader=False, daytrade_count=0)
    def get_all_positions(self):
        return [SimpleNamespace(asset_id="a",symbol="NVDA",exchange="NASDAQ",asset_class="us_equity",
            qty="2",side="long",market_value="300",cost_basis="250",unrealized_pl="50",
            unrealized_plpc=".2",current_price="150",lastday_price="145",change_today=".034")]
    def get_orders(self, filter=None):
        return [{"symbol":"NVDA","side":"buy","filled_qty":"2","filled_avg_price":"150",
            "filled_at":datetime.now(timezone.utc),"status":"filled"}]
    def get_portfolio_history(self, request=None):
        return SimpleNamespace(timestamp=[1,2], equity=[9900,10000], profit_loss=[0,100],
            profit_loss_pct=[0,.0101], base_value=9900)
    def close_all_positions(self, cancel_orders=False):
        self.closed += 1
        return [{"symbol":"NVDA","status":"accepted","cancel_orders":cancel_orders}]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSQUITO_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("ALPACA_ENABLE_ORDER_EXECUTION", raising=False)
    monkeypatch.delenv("ALPACA_LIVE_TRADING", raising=False)
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    module.CACHE.clear(); module.CALLS.clear(); module.EXIT_RESULTS.clear()
    fake = FakeClient()
    monkeypatch.setattr(module, "client", lambda: fake)
    module.app.config.update(TESTING=True)
    return module, module.app.test_client(), fake


def test_health_ready_and_security_headers(api):
    _, c, _ = api
    r = c.get("/health")
    assert r.status_code == 200 and r.json["status"] == "ok"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert c.get("/ready").json["status"] == "ready"
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200


def test_token_protects_api_and_query_sets_cookie(api, monkeypatch):
    _, c, _ = api
    monkeypatch.setenv("DASHBOARD_TOKEN", "owner-secret")
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"Authorization":"Bearer owner-secret"}).status_code == 200
    r = c.get("/?token=owner-secret")
    assert "mosquito_access=" in r.headers["Set-Cookie"]
    assert c.get("/api/status").status_code == 200


def test_config_persists_and_rejects_bad_numbers(api):
    module, c, _ = api
    r = c.post("/api/config", json={"allocation":987654321.25,"daily_goal":750})
    assert r.status_code == 200 and r.json["allocation"] == 987654321.25
    assert module.load_state()["daily_goal"] == 750
    for bad in (-1, "nan", math.inf):
        assert c.post("/api/config", json={"allocation":bad}).status_code == 400


def test_account_positions_dashboard_and_alerts_use_real_values(api):
    module, c, _ = api
    state=module.load_state();state["requested_investment"]=9000;module.save_state(state);module.CACHE.clear()
    assert c.get("/api/account").json["equity"] == "10000"
    assert c.get("/api/account").json["total_profit"] == 1000
    assert c.get("/api/account").json["mode"] == "paper"
    assert c.get("/api/positions").json["positions"][0]["symbol"] == "NVDA"
    board = c.get("/api/dashboard").json
    assert board["account"]["buying_power"] == "12000"
    assert board["positions"][0]["unrealized_pl"] == "50"
    assert board["trades_today"] == 1 and board["trades"][0]["symbol"] == "NVDA"
    assert board["performance"][0]["profit"] == 100
    assert c.get("/api/alerts").json["count"] == 0


def test_start_is_idempotent_and_caps_at_buying_power(api):
    module, c, fake = api
    first = c.post("/api/bot/start", json={"allocation":50000})
    assert first.status_code == 200
    assert first.json["allocation"] == 12000 and first.json["capped"] is True
    assert module.load_state()["requested_investment"] == 50000
    assert c.get("/api/status").json["investment_amount"] == 50000
    assert first.json["execution_enabled"] is False and fake.closed == 0
    again = c.post("/api/bot/start", json={"allocation":50000})
    assert again.json["already_running"] is True and fake.closed == 0
    stop = c.post("/api/bot/stop")
    assert stop.json["running"] is False and stop.json["positions_unchanged"] is True
    assert c.post("/api/bot/stop").json["already_stopped"] is True


def test_frontend_contract_fields_and_investment_amount(api):
    _, c, _ = api
    status = c.get("/api/status").json
    assert {"order_execution","investment_amount","risk_status","risk_detail"} <= status.keys()
    board = c.get("/api/dashboard").json
    assert {"positions_count","trades_today","win_rate","alerts_count","history",
            "positions","trades","performance","alerts"} <= board.keys()
    started = c.post("/api/bot/start", json={"investment_amount":321})
    assert started.status_code == 200 and started.json["allocation"] == 321


def test_exit_never_calls_broker_without_execution_gate(api):
    module, c, fake = api
    assert c.post("/api/bot/exit").status_code == 400
    r = c.post("/api/bot/exit", json={"confirm":"EXIT ALL POSITIONS"})
    assert r.status_code == 409 and r.json["ok"] is False and r.json["executed"] is False
    assert r.json["status"] == "blocked" and module.load_state()["exit_status"] == "blocked"
    assert fake.closed == 0


def test_exit_calls_paper_broker_only_with_gate(api, monkeypatch):
    module, c, fake = api
    monkeypatch.setenv("ALPACA_ENABLE_ORDER_EXECUTION", "true")
    assert module.paper() is True
    r = c.post("/api/bot/exit", json={"confirm":"EXIT ALL POSITIONS","request_id":"exit-1"})
    assert r.status_code == 202 and r.json["executed"] is True and r.json["mode"] == "paper"
    assert r.json["submitted"] is True and r.json["completed"] is False
    assert r.json["status"] == "pending" and module.load_state()["exit_status"] == "pending"
    assert fake.closed == 1
    replay = c.post("/api/bot/exit", json={"confirm":"EXIT ALL POSITIONS","request_id":"exit-1"})
    assert replay.status_code == 202 and replay.json["request_id"] == "exit-1"
    assert fake.closed == 1


def test_exit_records_broker_failure_without_claiming_completion(api, monkeypatch):
    module, c, fake = api
    monkeypatch.setenv("ALPACA_ENABLE_ORDER_EXECUTION", "true")
    def fail_close(cancel_orders=False):
        fake.closed += 1
        raise RuntimeError("secret broker detail")
    fake.close_all_positions = fail_close
    r = c.post("/api/bot/exit", json={"confirm":"EXIT ALL POSITIONS"})
    assert r.status_code == 503 and "secret" not in r.get_data(as_text=True)
    state = module.load_state()
    assert state["running"] is False and state["exit_status"] == "failed"
    assert fake.closed == 1


def test_exit_reports_partial_rejections(api, monkeypatch):
    module, c, fake = api
    monkeypatch.setenv("ALPACA_ENABLE_ORDER_EXECUTION", "true")
    fake.close_all_positions = lambda cancel_orders=False: [
        {"symbol":"NVDA","status":200}, {"symbol":"AMD","status":500,"body":"rejected"}]
    r = c.post("/api/bot/exit", json={"confirm":"EXIT ALL POSITIONS"})
    assert r.status_code == 502 and r.json["ok"] is False
    assert r.json["status"] == "partial_failure" and r.json["completed"] is False
    assert r.json["failures"][0]["symbol"] == "AMD"
    assert module.load_state()["exit_status"] == "partial_failure"


def test_scan_is_mocked_and_never_orders(api, monkeypatch):
    module, c, fake = api
    monkeypatch.setattr(module, "build_watchlist", lambda count: {
        "strategy":"V5.8 Master","count":count,"picks":[],"generated_at":"2026-01-01T00:00:00+00:00"})
    r = c.post("/api/scan", json={"count":50})
    assert r.status_code == 200 and r.json["count"] == 50 and fake.closed == 0
    assert c.post("/api/scan", json={"count":201}).status_code == 400


def test_broker_errors_are_sanitized(api, monkeypatch):
    module, c, _ = api
    def explode(): raise RuntimeError("SECRET upstream credential detail")
    monkeypatch.setattr(module, "account_data", explode)
    module.CACHE.clear()
    r = c.get("/api/account")
    assert r.status_code == 503
    assert "SECRET" not in r.get_data(as_text=True)


def test_start_preserves_request_when_broker_is_unavailable(api, monkeypatch):
    module, c, _ = api
    def explode(): raise RuntimeError("SECRET broker credential detail")
    monkeypatch.setattr(module, "account_data", explode)
    module.CACHE.clear()
    r = c.post("/api/bot/start", json={"investment_amount":50000})
    assert r.status_code == 503
    assert "SECRET" not in r.get_data(as_text=True)
    state = module.load_state()
    assert state["requested_investment"] == 50000
    assert state["allocation"] == 0
    assert state["running"] is False
    assert c.get("/api/status").json["investment_amount"] == 50000


def test_start_state_write_failure_is_sanitized(api, monkeypatch):
    module, c, _ = api
    def explode(_state): raise PermissionError("SECRET /data detail")
    monkeypatch.setattr(module, "save_state", explode)
    r = c.post("/api/bot/start", json={"investment_amount":50000})
    assert r.status_code == 500
    assert r.json["error"] == "Bot state could not be saved"
    assert "SECRET" not in r.get_data(as_text=True)
