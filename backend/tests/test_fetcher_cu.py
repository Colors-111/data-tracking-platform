from pathlib import Path
import types

import pandas as pd
import pytest

from backend.fetchers.base import build_fetcher

FIX = Path(__file__).parent / "fixtures" / "futures_inventory_cu.json"

@pytest.fixture
def cu_fetcher(conn, monkeypatch):
    src = conn.execute("SELECT * FROM sources WHERE id='cu-inventory'").fetchone()
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id='cu-inventory'").fetchall()
    # fixture 的日期列经 to_json 是 epoch-ms int（Task 4 实测）；
    # fallback 样本为字符串日期，覆盖字符串分支
    df = pd.read_json(FIX) if FIX.exists() else pd.DataFrame([
        {"日期": "2026-09-30", "库存": 102000.0},
        {"日期": "2026-10-09", "库存": 101500.0},
    ])
    import backend.fetchers.akshare_cu_inventory as mod
    # 接口本身返回近 60 日全量、与入参日期无关 → lambda symbol: df；
    # 统一用 monkeypatch（模块级直接赋值会跨测试泄漏）。
    # 桩参数名与生产调用 ak.futures_inventory_em(symbol=...) 对齐
    monkeypatch.setattr(mod, "ak",
                        types.SimpleNamespace(futures_inventory_em=lambda symbol: df))
    return build_fetcher(src["fetcher"], src, insts)

def test_returns_full_history(cu_fetcher):
    obs = cu_fetcher.fetch("2026-10-16")   # 传任意日期，接口本身返回近60日全量
    assert len(obs) >= 2
    assert all(o.metric == "inventory" for o in obs)
    assert all(o.instrument_id == "cu-inventory" for o in obs)

def test_dates_normalized(cu_fetcher):
    # fixture 存在 → 走 epoch-ms 分支；字符串日期分支由 fallback 样本形态覆盖
    obs = cu_fetcher.fetch("2026-10-16")
    assert len(obs) > 0
    assert all(len(o.trade_date) == 10 and o.trade_date[4] == "-" for o in obs)

def test_fetch_empty_df_returns_empty(cu_fetcher, monkeypatch):
    import backend.fetchers.akshare_cu_inventory as mod
    monkeypatch.setattr(mod, "ak",
                        types.SimpleNamespace(futures_inventory_em=lambda symbol: None))
    assert cu_fetcher.fetch("2026-10-16") == []   # 生产路径：接口返回空

def test_missing_column_raises(cu_fetcher, monkeypatch):
    # 锁定 _col fail-loud：缺库存列时抛 ValueError，而非静默产出错误数据
    import backend.fetchers.akshare_cu_inventory as mod
    monkeypatch.setattr(mod, "ak", types.SimpleNamespace(
        futures_inventory_em=lambda symbol: pd.DataFrame([{"日期": "2026-10-15"}])))
    with pytest.raises(ValueError):
        cu_fetcher.fetch("2026-10-16")

def test_nan_inventory_skipped(cu_fetcher, monkeypatch):
    # 库存=NaN 的行被跳过：不产出 Observation 也不报错
    import backend.fetchers.akshare_cu_inventory as mod
    monkeypatch.setattr(mod, "ak", types.SimpleNamespace(
        futures_inventory_em=lambda symbol: pd.DataFrame([
            {"日期": "2026-10-14", "库存": 9407.0},
            {"日期": "2026-10-15", "库存": float("nan")},
        ])))
    obs = cu_fetcher.fetch("2026-10-16")
    assert len(obs) == 1 and obs[0].trade_date == "2026-10-14"
