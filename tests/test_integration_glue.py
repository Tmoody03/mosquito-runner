import importlib,sys
from types import SimpleNamespace
import pytest


@pytest.fixture()
def api(tmp_path,monkeypatch):
    monkeypatch.setenv("MOSQUITO_STATE_FILE",str(tmp_path/"state.json"))
    monkeypatch.delenv("RAILWAY_ENVIRONMENT",raising=False)
    monkeypatch.delenv("MOSQUITO_RUNTIME_ENABLED",raising=False)
    sys.modules.pop("app",None)
    module=importlib.import_module("app")
    return module,None,None


def test_open_orchestration_glues_selection_to_paper_lifecycle(api, monkeypatch):
    module, _client, _fake = api
    monkeypatch.setenv("ALPACA_ENABLE_ORDER_EXECUTION","true")
    broker=SimpleNamespace()
    monkeypatch.setattr(module,"client",lambda:broker)
    monkeypatch.setattr(module,"account_data",lambda:{"buying_power":"60000"})
    picks=[{"ticker":f"AI{i:03}","rank":i,"score":100-i,"price":10+i,"eligible":True} for i in range(1,101)]
    monkeypatch.setattr(module,"tradable_picks",lambda _broker,count=100:picks[:count])
    calls=[]
    class Life:
        def reconcile(self):return {"positions":{},"orders":{}}
        def enter(self,selected,allocation):calls.append((selected,allocation));return {"orders":{str(i):{} for i in range(50)}}
    monkeypatch.setattr(module,"lifecycle_for",lambda _broker:Life())
    monkeypatch.setattr(module,"ensure_engine",lambda:None)
    state=module.load_state();state.update(requested_investment=50000,armed=True);module.save_state(state)
    result=module.orchestrate_open(session_date="2026-09-17",idempotency_key="open-1",paper_only=True)
    assert result["selected"]==50 and result["orders"]==50 and result["allocation"]==50000
    assert len(calls)==1 and len(calls[0][0])==100
    saved=module.load_state();assert saved["running"] is True and saved["armed"] is True


def test_open_orchestration_never_touches_broker_when_execution_is_disabled(api,monkeypatch):
    module,_client,_fake=api
    state=module.load_state();state.update(requested_investment=50000,armed=True);module.save_state(state)
    monkeypatch.delenv("ALPACA_ENABLE_ORDER_EXECUTION",raising=False)
    monkeypatch.setattr(module,"client",lambda:(_ for _ in ()).throw(AssertionError("broker must not be called")))
    result=module.orchestrate_open(session_date="2026-09-17",idempotency_key="open-1",paper_only=True)
    assert result=={"session_date":"2026-09-17","status":"broker_execution_disabled","orders":0,"paper_only":True}
    assert module.load_state()["running"] is False


def test_paper_adapter_refuses_orders_when_execution_is_disabled(api,monkeypatch):
    module,_client,_fake=api
    submitted=[]
    broker=SimpleNamespace(submit_order=lambda order_data=None:submitted.append(order_data))
    monkeypatch.delenv("ALPACA_ENABLE_ORDER_EXECUTION",raising=False)
    with pytest.raises(RuntimeError,match="execution is disabled"):
        module.PaperBrokerAdapter(broker).submit_order({"symbol":"NVDA","notional":100,"side":"buy","client_order_id":"test"})
    assert submitted==[]


def test_disarmed_open_does_not_touch_broker(api,monkeypatch):
    module,_client,_fake=api
    state=module.load_state();state["armed"]=False;module.save_state(state)
    monkeypatch.setattr(module,"client",lambda:(_ for _ in ()).throw(AssertionError("broker must not be called")))
    result=module.orchestrate_open(session_date="2026-09-17",idempotency_key="open-1",paper_only=True)
    assert result["status"]=="disarmed"


def test_live_launch_is_rejected_even_when_requested(api):
    module,_client,_fake=api
    with pytest.raises(RuntimeError,match="paper-only"):
        module.orchestrate_open(session_date="2026-09-17",idempotency_key="open-1",paper_only=False)
