from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scheduler import MarketScheduler


ET = ZoneInfo("America/New_York")


def clock(at, is_open=True):
    return SimpleNamespace(timestamp=at, is_open=is_open, next_open=at)


def session(day, opens="09:30", closes="16:00"):
    return SimpleNamespace(date=day, open=opens, close=closes)


def scheduler(tmp_path, at, rows, callback, is_open=True):
    return MarketScheduler(lambda: clock(at, is_open), lambda **_: rows, callback,
                           state_path=tmp_path / "runs.json")


def test_waits_until_0950_eastern_then_runs_once_and_survives_restart(tmp_path):
    calls = []
    day = date(2026, 9, 17)
    before = datetime(2026, 9, 17, 9, 29, tzinfo=ET)
    assert scheduler(tmp_path, before, [session(day)], lambda **kw: calls.append(kw)).tick()["status"] == "waiting"
    opened = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    waiting = scheduler(tmp_path, opened, [session(day)], lambda **kw: calls.append(kw)).tick()
    assert waiting["status"] == "waiting" and waiting["starts_at"].endswith("09:50:00-04:00")
    launch = datetime(2026, 9, 17, 9, 50, tzinfo=ET)
    first = scheduler(tmp_path, launch, [session(day)], lambda **kw: calls.append(kw)).tick()
    assert first["status"] == "completed"
    assert calls == [{"session_date": "2026-09-17", "idempotency_key": "mosquito-paper-open-2026-09-17", "paper_only": True}]
    assert scheduler(tmp_path, launch, [session(day)], lambda **kw: calls.append(kw)).tick()["status"] == "already_completed"
    assert len(calls) == 1


def test_preflight_runs_once_at_0930_and_entry_waits_until_0950(tmp_path):
    day=date(2026,9,17);calls=[];preflights=[]
    def make(at):
        return MarketScheduler(lambda:clock(at),lambda **_:[session(day)],lambda **kw:calls.append(kw),
            state_path=tmp_path/"two-phase.json",preflight=lambda **kw:preflights.append(kw) or {"locked":True})
    opened=datetime(2026,9,17,9,30,tzinfo=ET)
    result=make(opened).tick()
    assert result["status"]=="waiting" and result["preflight_complete"] is True
    assert preflights==[{"session_date":"2026-09-17","paper_only":True}] and calls==[]
    assert make(datetime(2026,9,17,9,49,tzinfo=ET)).tick()["status"]=="waiting"
    assert len(preflights)==1 and calls==[]
    assert make(datetime(2026,9,17,9,50,tzinfo=ET)).tick()["status"]=="completed"
    assert len(calls)==1


def test_restart_after_missed_open_recovers_during_session(tmp_path):
    calls = []
    day = date(2026, 9, 17)
    at = datetime(2026, 9, 17, 13, 45, tzinfo=ET)
    result = scheduler(tmp_path, at, [session(day)], lambda **kw: calls.append(kw) or "started").tick()
    assert result["status"] == "completed" and result["result"] == "started"


@pytest.mark.parametrize("day", [date(2026, 9, 19), date(2026, 9, 20), date(2026, 12, 25)])
def test_weekends_and_holiday_do_not_run(tmp_path, day):
    calls = []
    at = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=10)
    assert scheduler(tmp_path, at, [], lambda **kw: calls.append(kw)).tick() == {
        "status": "closed", "reason": "non_trading_day"}
    assert calls == []


def test_dst_uses_market_timezone_for_summer_and_winter(tmp_path):
    calls = []
    summer = date(2026, 7, 1)
    # 13:50 UTC is 09:50 EDT.
    summer_at = datetime(2026, 7, 1, 13, 50, tzinfo=timezone.utc)
    assert scheduler(tmp_path / "summer", summer_at, [session(summer)], lambda **kw: calls.append(kw)).tick()["status"] == "completed"
    winter = date(2026, 12, 1)
    # 14:50 UTC is 09:50 EST.
    winter_at = datetime(2026, 12, 1, 14, 50, tzinfo=timezone.utc)
    assert scheduler(tmp_path / "winter", winter_at, [session(winter)], lambda **kw: calls.append(kw)).tick()["status"] == "completed"
    assert len(calls) == 2


def test_broker_clock_can_block_exceptional_closure(tmp_path):
    day = date(2026, 9, 17)
    at = datetime(2026, 9, 17, 10, tzinfo=ET)
    assert scheduler(tmp_path, at, [session(day)], lambda **_: pytest.fail("must not run"), is_open=False).tick()["status"] == "closed"


def test_failure_is_sanitized_and_retried_with_same_key(tmp_path):
    day = date(2026, 9, 17)
    at = datetime(2026, 9, 17, 10, tzinfo=ET)
    keys = []
    def fail(**kw):
        keys.append(kw["idempotency_key"])
        raise RuntimeError("credential secret")
    assert scheduler(tmp_path, at, [session(day)], fail).tick()["status"] == "failed"
    result = scheduler(tmp_path, at, [session(day)], lambda **kw: keys.append(kw["idempotency_key"])).tick()
    assert result["status"] == "completed"
    assert keys == ["mosquito-paper-open-2026-09-17"] * 2


def test_live_mode_is_impossible(tmp_path):
    with pytest.raises(ValueError, match="paper-only"):
        MarketScheduler(lambda: None, lambda **_: [], lambda **_: None,
                        state_path=tmp_path / "x", paper_only=False)
