import importlib
import math
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


class FakeClient:
    def __init__(self):
        self.closed = 0
        self.submitted = []
    def get_account(self):
        return SimpleNamespace(id="acct", status="ACTIVE", currency="USD", cash="8000",
            portfolio_value="10000", equity="10000", last_equity="9900", buying_power="12000",
            daytrading_buying_power="12000", regt_buying_power="12000", trading_blocked=False,
            transfers_blocked=False, account_blocked=False, pattern_day_trader=False, daytrade_count=0)
    def get_clock(self):
        return SimpleNamespace(is_open=False)
    def get_all_positions(self):
        return [SimpleNamespace(asset_id="a",symbol="NVDA",exchange="NASDAQ",asset_class="us_equity",
            qty="2",side="long",market_value="300",cost_basis="250",avg_entry_price="125",unrealized_pl="50",
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
    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        return SimpleNamespace(id="order-1", status="accepted")


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSQUITO_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("ALPACA_ENABLE_ORDER_EXECUTION", raising=False)
    monkeypatch.delenv("ALPACA_LIVE_TRADING", raising=False)
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    module.CACHE.clear(); module.CALLS.clear(); module.EXIT_RESULTS.clear()
    module.BROKER_HEALTH_CACHE=None;module.BROKER_HEALTH_EXPIRES=0;module.BROKER_HEALTH_FUTURE=None
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
    assert c.get("/static/mosquito-hero.webp").status_code == 200
    assert c.get("/static/mosquito-hero-profit.webp").status_code == 200
    assert c.get("/static/mosquito.svg").status_code == 404


def test_public_broker_health_authenticates_read_only_without_account_data(api,monkeypatch):
    module,c,_=api
    monkeypatch.setenv("ALPACA_API_KEY","paper-key");monkeypatch.setenv("ALPACA_SECRET_KEY","paper-secret")
    response=c.get("/broker-health")
    assert response.status_code==200
    assert response.json=={"status":"ok","configured":True,"authenticated":True,"account_readable":True,"clock_readable":True,"paper_mode":True,"checked_at":response.json["checked_at"]}
    body=response.get_data(as_text=True)
    assert "paper-key" not in body and "paper-secret" not in body and "buying_power" not in body and "account" not in body.lower().replace("account_readable","")


def test_broker_health_failure_is_degraded_and_redacted(api,monkeypatch,caplog):
    module,c,_=api
    from alpaca.common.exceptions import APIError
    secret="broker-health-secret"
    monkeypatch.setenv("ALPACA_API_KEY","paper-key");monkeypatch.setenv("ALPACA_SECRET_KEY",secret)
    module.BROKER_HEALTH_CACHE=None;module.BROKER_HEALTH_EXPIRES=0;module.BROKER_HEALTH_FUTURE=None
    def fail():raise APIError(module.json.dumps({"code":40110000,"message":f"bad credentials {secret}"}))
    monkeypatch.setattr(module,"_broker_health_probe",fail)
    with caplog.at_level("WARNING"):
        response=c.get("/broker-health")
    assert response.status_code==503 and response.json["status"]=="degraded"
    assert response.json["authenticated"] is False and response.json["paper_mode"] is True
    output=response.get_data(as_text=True)+" "+" ".join(r.getMessage() for r in caplog.records)
    assert secret not in output and "paper-key" not in output


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
    assert fake.closed == 0 and len(fake.submitted) == 1
    replay = c.post("/api/bot/exit", json={"confirm":"EXIT ALL POSITIONS","request_id":"exit-1"})
    assert replay.status_code == 202 and replay.json["request_id"] == "exit-1"
    assert fake.closed == 0 and len(fake.submitted) == 1


def test_exit_records_broker_failure_without_claiming_completion(api, monkeypatch):
    module, c, fake = api
    monkeypatch.setenv("ALPACA_ENABLE_ORDER_EXECUTION", "true")
    def fail_close(order_data=None):
        raise RuntimeError("secret broker detail")
    fake.submit_order = fail_close
    r = c.post("/api/bot/exit", json={"confirm":"EXIT ALL POSITIONS"})
    assert r.status_code == 502 and "secret" not in r.get_data(as_text=True)
    state = module.load_state()
    assert state["running"] is False and state["exit_status"] == "partial_failure"
    assert fake.closed == 0


def test_exit_reports_partial_rejections(api, monkeypatch):
    module, c, fake = api
    monkeypatch.setenv("ALPACA_ENABLE_ORDER_EXECUTION", "true")
    fake.submit_order = lambda order_data=None: (_ for _ in ()).throw(RuntimeError("rejected"))
    r = c.post("/api/bot/exit", json={"confirm":"EXIT ALL POSITIONS"})
    assert r.status_code == 502 and r.json["ok"] is False
    assert r.json["status"] == "partial_failure" and r.json["completed"] is False
    assert r.json["failures"][0]["symbol"] == "NVDA"
    assert module.load_state()["exit_status"] == "partial_failure"


def test_scan_is_mocked_and_never_orders(api, monkeypatch):
    module, c, fake = api
    monkeypatch.setattr(module, "build_watchlist", lambda count: {
        "strategy":"V5.8 Master","count":count,"picks":[],"generated_at":"2026-01-01T00:00:00+00:00"})
    r = c.post("/api/scan", json={"count":50})
    assert r.status_code == 200 and r.json["count"] == 0 and r.json["requested_count"] == 50 and fake.closed == 0
    assert c.post("/api/scan", json={"count":201}).status_code == 400


def test_broker_errors_are_sanitized(api, monkeypatch):
    module, c, _ = api
    def explode(): raise RuntimeError("SECRET upstream credential detail")
    monkeypatch.setattr(module, "account_data", explode)
    module.CACHE.clear()
    r = c.get("/api/account")
    assert r.status_code == 503
    assert "SECRET" not in r.get_data(as_text=True)


def test_alpaca_api_error_logs_safe_diagnostics_without_secrets(api,monkeypatch,caplog):
    module,c,_=api
    from alpaca.common.exceptions import APIError
    secret="paper-secret-value"
    monkeypatch.setenv("ALPACA_SECRET_KEY",secret)
    def explode():raise APIError(module.json.dumps({"code":40110000,"message":f"authentication failed {secret}"}))
    monkeypatch.setattr(module,"account_data",explode);module.CACHE.clear()
    with caplog.at_level("WARNING"):
        response=c.get("/api/account")
    logged=" ".join(record.getMessage() for record in caplog.records)
    assert response.status_code==503
    assert '"operation":"get_account"' in logged and '"endpoint":"/v2/account"' in logged
    assert '"code":"40110000"' in logged and '"message":"authentication failed [REDACTED]"' in logged
    assert secret not in logged and "Authorization" not in logged


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


def test_green_ribbon_simulation_is_paper_only_and_persistent(tmp_path, monkeypatch):
    import simulator
    monkeypatch.setattr(simulator,"PATH",tmp_path/"simulation.json")
    monkeypatch.setattr(simulator,"build_watchlist",lambda count:{"picks":[
        {"ticker":"NVDA","price":100,"rank":1,"score":99}],"april_trade_allowed":True,
        "generated_at":"2026-09-01T00:00:00+00:00"})
    monkeypatch.setattr(simulator,"_closes",lambda symbols:__import__("pandas").DataFrame(
        {"NVDA":[100,101,103]},index=__import__("pandas").date_range("2026-09-11",periods=3)))
    data=simulator.start(50000)
    assert data["positions"][0]["qty"]==500
    report=simulator.value()
    assert report["current_value"]==51500 and report["day_profit"]==1000
    assert report["ribbon_value"]==1000 and report["ribbon_earned"] is True
    assert simulator.load()["history"][-1]["value"]==51500


def test_dashboard_exposes_green_ribbon_when_enabled(api,monkeypatch):
    module,c,_=api
    monkeypatch.setenv("MOSQUITO_ENABLE_SIMULATION","true")
    monkeypatch.setattr(module.simulator,"value",lambda:{"status":"FINAL · MARKET CLOSED","current_value":51000,"eod_value":51000,"ribbon_value":1000,"ribbon_earned":True})
    module.CACHE.clear();board=c.get("/api/dashboard").json
    assert board["green_ribbon"]["current_value"]==51000


def test_simulator_never_voluntarily_sells_below_purchase(tmp_path,monkeypatch):
    import simulator
    monkeypatch.setattr(simulator,"PATH",tmp_path/"simulation.json")
    simulator.save({**simulator.EMPTY,"active":True,"starting_value":2000,"cash":0,"positions":[
        {"symbol":"WIN","qty":10,"entry_price":100,"entry_value":1000},
        {"symbol":"LOSS","qty":10,"entry_price":100,"entry_value":1000}]})
    monkeypatch.setattr(simulator,"_closes",lambda symbols:__import__("pandas").DataFrame(
        {"WIN":[110],"LOSS":[90]},index=__import__("pandas").date_range("2026-09-15",periods=1)))
    report=simulator.exit_all();saved=simulator.load()
    assert [p["symbol"] for p in saved["positions"]]==["LOSS"]
    assert saved["cash"]==1100 and report["blocked_below_purchase"]==["LOSS"]
    assert report["completed"] is False


def test_trailing_rule_arms_at_gain_and_never_signals_below_entry():
    from protection import protected_state
    rising=protected_state(100,110.70)
    assert rising["peak_price"]==110.70 and not rising["should_sell"]
    triggered=protected_state(100,110.42,previous_peak=rising["peak_price"])
    assert triggered["should_sell"] is True
    assert triggered["protected_floor"]==100
    protected=protected_state(100,90,previous_peak=110.70)
    assert protected["should_sell"] is False
    assert protected["status"]=="PROTECTED_BELOW_ENTRY"


def test_entry_gate_requires_half_percent_above_session_open(api):
    module, _client, _fake = api
    assert module.entry_signal_met(100,100.24,0.0025) is False
    assert module.entry_signal_met(100,100.25,0.0025) is True
    assert module.entry_signal_met(100,101.00) is True
    assert module.entry_signal_met(0,101.00) is False
    assert module.entry_signal_met(None,101.00) is False


def test_automated_trading_waits_until_0950_eastern(api):
    module, _client, _fake = api
    before=SimpleNamespace(is_open=True,timestamp=datetime(2026,9,17,13,49,tzinfo=timezone.utc))
    launch=SimpleNamespace(is_open=True,timestamp=datetime(2026,9,17,13,50,tzinfo=timezone.utc))
    assert module.paper_trade_window_open(SimpleNamespace(get_clock=lambda:before)) is False
    assert module.paper_trade_window_open(SimpleNamespace(get_clock=lambda:launch)) is True


def test_broker_exit_holds_position_below_purchase(api, monkeypatch):
    module,c,fake=api
    monkeypatch.setenv("ALPACA_ENABLE_ORDER_EXECUTION","true")
    fake.get_all_positions=lambda:[SimpleNamespace(symbol="LOSS",qty="2",avg_entry_price="125",current_price="100")]
    r=c.post("/api/bot/exit",json={"confirm":"EXIT ALL POSITIONS"})
    assert r.status_code==409 and r.json["status"]=="partially_blocked"
    assert r.json["blocked_below_purchase"][0]["symbol"]=="LOSS"
    assert fake.submitted==[] and fake.closed==0


def test_engine_persists_peak_and_submits_one_protected_sell(tmp_path,monkeypatch):
    import engine
    monkeypatch.setattr(engine,"PATH",tmp_path/"engine.json")
    fake=FakeClient()
    fake.get_all_positions=lambda:[SimpleNamespace(symbol="NVDA",qty="2",avg_entry_price="100",current_price="110.70")]
    first=engine.cycle(fake,submit_enabled=True)
    assert first["positions"]["NVDA"]["peak_price"]==110.70 and fake.submitted==[]
    fake.get_all_positions=lambda:[SimpleNamespace(symbol="NVDA",qty="2",avg_entry_price="100",current_price="110.42")]
    second=engine.cycle(fake,submit_enabled=True)
    row=second["positions"]["NVDA"]
    assert row["pending_order_id"]=="order-1" and len(fake.submitted)==1
    assert float(fake.submitted[0].limit_price)>=100
    engine.cycle(fake,submit_enabled=True)
    assert len(fake.submitted)==1
