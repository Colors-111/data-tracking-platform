import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from backend.fetchers._util import normalize_date
from backend.fetchers.base import build_fetcher

FIX_SSE = Path(__file__).parent / "fixtures" / "sse_etf_scale.json"
FIX_SZSE = Path(__file__).parent / "fixtures" / "szse_etf_scale.json"


@pytest.fixture
def mixed_env(conn, monkeypatch):
    return _env(conn, monkeypatch, "csi500-etf")


def _env(conn, monkeypatch, source_id, sse=None, szse=None):
    """按 source_id 构建 fetcher，akshare 双所接口默认回放 fixture，
       可传入带探针的 fake 覆盖。"""
    src = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
        (source_id,)).fetchall()
    # to_json 会把日期列写成 epoch 毫秒、代码列可能写成数字，read_json 显式 dtype=str
    sse_df = pd.read_json(FIX_SSE, dtype={"基金代码": str})
    szse_df = pd.read_json(FIX_SZSE, dtype={"基金代码": str})
    import backend.fetchers.akshare_etf_scale as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse", sse or (lambda d: sse_df))
    monkeypatch.setattr(mod.ak, "fund_scale_daily_szse",
                        szse or (lambda start_date, end_date, symbol=None: szse_df))
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


# ---------- parity 测试（_util 抽取后固化跨 fetcher 一致契约） ----------

def test_dispatch_short_circuits(conn, monkeypatch):
    # 分流短路：单所源不得触发另一所接口（各只调 1 次即证明无交叉调用）
    calls = {"sse": [], "szse": []}
    sse_df = pd.read_json(FIX_SSE, dtype={"基金代码": str})
    szse_df = pd.read_json(FIX_SZSE, dtype={"基金代码": str})

    def fake_sse(d):
        calls["sse"].append(d)
        return sse_df

    def fake_szse(start_date, end_date, symbol=None):
        calls["szse"].append((start_date, end_date))
        return szse_df

    _env(conn, monkeypatch, "chinext-etf", sse=fake_sse, szse=fake_szse).fetch("2026-10-14")   # 全深交所
    _env(conn, monkeypatch, "star50-etf", sse=fake_sse, szse=fake_szse).fetch("2026-10-14")    # 全上交所
    assert len(calls["szse"]) == 1   # 只有 chinext 调了深交所
    assert len(calls["sse"]) == 1    # 只有 star50 调了上交所


def test_szse_failure_isolated_sse_still_produces(mixed_env, monkeypatch, caplog):
    # v1.2.1 契约变更（源级原子 → 分支级失败隔离）：深交所断连不再拖死
    # 上交所——混合源（csi500：159922/512500/159820）上交所品种照常产出，
    # 告警含分支名与错误摘要；两所全部失败才抛（见下）
    import backend.fetchers.akshare_etf_scale as mod

    def boom(start_date, end_date, symbol=None):
        raise RuntimeError("szse down")

    monkeypatch.setattr(mod.ak, "fund_scale_daily_szse", boom)
    obs = mixed_env.fetch("2026-10-14")
    got = {o.instrument_id: o for o in obs}
    assert set(got) == {"512500"}
    assert got["512500"].metric == "share"
    assert "SZSE" in caplog.text and "szse down" in caplog.text


def test_both_exchanges_fail_reraises(mixed_env, monkeypatch):
    # 两所全部失败 → 仍抛（run_source 能标 failed；静默全空更危险）
    import backend.fetchers.akshare_etf_scale as mod

    def boom(*a, **k):
        raise RuntimeError("both down")

    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse", boom)
    monkeypatch.setattr(mod.ak, "fund_scale_daily_szse", boom)
    with pytest.raises(RuntimeError, match="全部分支失败"):
        mixed_env.fetch("2026-10-14")


@pytest.mark.parametrize("v,fb,expected", [
    ("2026-10-14", None, "2026-10-14"),                       # str
    ("2026/10/14", None, "2026-10-14"),                       # 斜杠（cu 口径）
    (pd.Timestamp("2026-10-14 15:00"), None, "2026-10-14"),   # Timestamp
    (np.datetime64("2026-10-14"), None, "2026-10-14"),        # datetime64
    (1791936000000, None, "2026-10-14"),                      # epoch-ms（UTC 锚定）
    (20261014, None, "2026-10-14"),                           # YYYYMMDD 整数
    (float("nan"), None, None),                               # NaN → None（cu 跳行契约）
    (pd.NaT, "2026-10-14", "2026-10-14"),                     # NaN → fallback（etf_scale 兜底）
])
def test_normalize_date_forms(v, fb, expected):
    assert normalize_date(v, fallback=fb) == expected


def test_missing_column_raises(mixed_env, monkeypatch):
    # 锁定 fail-loud（对齐 test_fetcher_sse_etf.py::test_missing_column_raises）：
    # 缺份额列时抛 ValueError，而非静默产出错误数据
    import backend.fetchers.akshare_etf_scale as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse",
                        lambda d: pd.DataFrame([{"基金代码": "512500"}]))
    with pytest.raises(ValueError):
        mixed_env.fetch("2026-10-14")
