import pandas as pd
import pytest
from pathlib import Path

from backend.fetchers.base import build_fetcher

FIX_SSE = Path(__file__).parent / "fixtures" / "sse_etf_scale.json"
FIX_SZSE = Path(__file__).parent / "fixtures" / "szse_etf_scale.json"


@pytest.fixture
def mixed_env(conn, monkeypatch):
    src = conn.execute("SELECT * FROM sources WHERE id='csi500-etf'").fetchone()
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id='csi500-etf' ORDER BY sort_order"
    ).fetchall()
    # to_json 会把日期列写成 epoch 毫秒、代码列可能写成数字，read_json 显式 dtype=str
    sse_df = pd.read_json(FIX_SSE, dtype={"基金代码": str})
    szse_df = pd.read_json(FIX_SZSE, dtype={"基金代码": str})
    import backend.fetchers.akshare_etf_scale as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse", lambda d: sse_df)
    monkeypatch.setattr(mod.ak, "fund_scale_daily_szse",
                        lambda start_date, end_date, symbol=None: szse_df)
    return build_fetcher(src["fetcher"], src, insts)


def test_fetch_dispatches_by_exchange(mixed_env):
    obs = mixed_env.fetch("2026-10-14")
    ids = {o.instrument_id for o in obs}
    assert {"159922", "512500", "159820"} <= ids      # 沪深混合全部命中
    assert all(o.metric == "share" and o.trade_date == "2026-10-14" for o in obs)


def test_fetch_converts_to_yi(mixed_env):
    obs = mixed_env.fetch("2026-10-14")
    by_id = {o.instrument_id: o for o in obs}
    # 两所接口单位都是"份"；fixture 实测 159922 嘉实 = 2.752677e9 份 = 27.52677 亿份
    assert by_id["159922"].value == pytest.approx(27.52677, abs=0.001)
    assert by_id["512500"].value == pytest.approx(18.136088, abs=0.001)  # 上交所分支
    assert by_id["159820"].value == pytest.approx(13.81408, abs=0.001)   # 深交所分支


def test_empty_inputs(mixed_env, monkeypatch):
    import backend.fetchers.akshare_etf_scale as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse", lambda d: pd.DataFrame())
    monkeypatch.setattr(mod.ak, "fund_scale_daily_szse",
                        lambda start_date, end_date, symbol=None: None)
    assert mixed_env.fetch("2026-10-14") == []


def test_lag_days_is_one(mixed_env):
    assert mixed_env.lag_days == 1
