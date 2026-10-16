import json
from pathlib import Path
import pandas as pd
import pytest
from backend.app import db
from backend.fetchers.base import build_fetcher

FIX = Path(__file__).parent / "fixtures" / "sse_etf_scale.json"

@pytest.fixture
def sse_env(conn, monkeypatch):
    src = conn.execute("SELECT * FROM sources WHERE id='hs300-etf'").fetchone()
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id='hs300-etf' ORDER BY sort_order"
    ).fetchall()
    # 真实列名（Task 4 实测）：['序号','基金代码','基金简称','ETF类型','统计日期','基金份额']
    # 基金份额单位是"份"；to_json 会把代码列写成数字，read_json 必须显式 dtype=str
    df = (pd.read_json(FIX, dtype={"基金代码": str}) if FIX.exists()
          else pd.DataFrame([
              {"基金代码": "510300", "基金简称": "300ETF", "基金份额": 24119590000},
              {"基金代码": "510310", "基金简称": "沪深300ETF易方达", "基金份额": 16726000000},
          ]))
    import backend.fetchers.akshare_sse_etf as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse", lambda d: df)
    return build_fetcher(src["fetcher"], src, insts)

def test_fetch_filters_target_codes(sse_env):
    obs = sse_env.fetch("2026-09-30")
    ids = {o.instrument_id for o in obs}
    assert {"510300", "510310"} <= ids and len(ids) == 5
    assert all(o.metric == "share" and o.trade_date == "2026-09-30" for o in obs)

def test_fetch_converts_to_yi(sse_env):
    # 接口返回"份"（510300≈2.41e10 份），入库统一亿份
    obs = sse_env.fetch("2026-09-30")
    by_id = {o.instrument_id: o for o in obs}
    assert by_id["510300"].value == pytest.approx(241.1959, abs=0.01)

def test_lag_days_is_one(sse_env):
    assert sse_env.lag_days == 1
