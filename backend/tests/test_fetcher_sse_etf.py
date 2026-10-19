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
    assert {"510300", "510310"} <= ids and len(ids) == 9   # 5+4 SSE 新成员（SZSE 2 只不在上交所快照）
    assert all(o.metric == "share" and o.trade_date == "2026-09-30" for o in obs)

def test_fetch_converts_to_yi(sse_env):
    # 接口返回"份"（510300≈2.41e10 份），入库统一亿份
    obs = sse_env.fetch("2026-09-30")
    by_id = {o.instrument_id: o for o in obs}
    assert by_id["510300"].value == pytest.approx(241.1959, abs=0.01)
    assert by_id["515330"].value > 0   # 天弘沪深300（v1.3.x 补齐品种）

def test_lag_days_is_one(sse_env):
    assert sse_env.lag_days == 1

def test_fetch_empty_df_returns_empty(sse_env, monkeypatch):
    # T+1 滞后日接口可能返回空表，生产路径须返回 [] 而非报错
    import backend.fetchers.akshare_sse_etf as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse", lambda d: pd.DataFrame())
    assert sse_env.fetch("2026-09-30") == []

def test_missing_column_raises(sse_env, monkeypatch):
    # 锁定 _col fail-loud：缺份额列时抛 ValueError，而非静默产出错误数据
    import backend.fetchers.akshare_sse_etf as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse",
                        lambda d: pd.DataFrame([{"基金代码": "510300"}]))
    with pytest.raises(ValueError):
        sse_env.fetch("2026-09-30")

def test_nan_name_normalized_to_empty(sse_env, monkeypatch):
    # 列存在但值为 NaN 时 raw.name 归一为空串，而非 "nan"/"<NA>"
    import backend.fetchers.akshare_sse_etf as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse", lambda d: pd.DataFrame(
        [{"基金代码": "510300", "基金简称": float("nan"), "基金份额": 24119590000}]))
    (obs,) = sse_env.fetch("2026-09-30")
    assert obs.raw == {"name": ""}
