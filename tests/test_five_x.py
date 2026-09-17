from datetime import datetime, timedelta, timezone

import pandas as pd

import five_x


def market_frame():
    index=pd.date_range("2026-01-01",periods=127,freq="B")
    close=pd.DataFrame({
        "GOOD":[10+i*.08 for i in range(127)],
        "BLOW":[2+i*.01 for i in range(105)]+[3+i*.20 for i in range(22)],
        "ILLIQ":[10+i*.02 for i in range(127)],
    },index=index)
    volume=pd.DataFrame({"GOOD":[500_000]*127,"BLOW":[900_000]*127,"ILLIQ":[1_000]*127},index=index)
    return close,volume


def test_market_rank_is_liquid_and_penalizes_blowoff():
    close,volume=market_frame();rows=five_x.rank_market(close,volume,count=10)
    assert {row["ticker"] for row in rows}=={"GOOD","BLOW"}
    assert rows[0]["ticker"]=="GOOD"


def test_virtual_ledger_is_independent_and_never_sells_below_entry(tmp_path,monkeypatch):
    monkeypatch.setattr(five_x,"PATH",tmp_path/"5x.json")
    rows=[{"ticker":f"X{i}","rank":i,"score":100-i,"price":10,"eligible":True} for i in range(1,8)]
    five_x.lock_selection("2026-09-17",rows,50000)
    five_x.buy_locked("2026-09-17",{row["ticker"]:10 for row in rows})
    state=five_x.mark({row["ticker"]:9 for row in rows})
    assert len(state["positions"])==7 and not state["closed"]
    five_x.mark({row["ticker"]:11 for row in rows})
    state=five_x.mark({row["ticker"]:10.99 for row in rows})
    assert not state["positions"] and len(state["closed"])==7
    assert all(row["exit_price"]>=row["entry_price"] for row in state["closed"])
