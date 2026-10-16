"""composite 编排化（v1.2 增量）单测：按 code 家族把宏观/贵金属分支委托给
   backend/fetchers/akshare_macro.py 的既有子 fetcher：

   - LME-{CU,AL}-{STOCK,WARRANT} → LmeStockFetcher（同一宽表接口一次调用
     同时供库存 inventory 与注册仓单 warehouse 两类成分）
   - LME-{CU,AL}-POS             → LmeHoldingFetcher（positioning）
   - CFTC-AU                     → CftcNcFetcher（positioning，周频）
   - GOLD-RESERVE                → GoldReserveFetcher（reserve，月频锚月末）
   - AU9999 → SgeSpotFetcher、XAU → LondonSpotFetcher：并行任务落地中的
     可选分支——类名延迟解析（可注入），缺失时 log.warning 跳过、不 fail
     （伦敦金接口由对方负责，类落地后自动接入）

   契约（对齐既有 composite 三支）：各分支观测合并返回；任一分支异常整体抛
   （源级原子）；无成分的分支短路、不触接口；全部分支空 → []。
   未识别 code（如 gold 源上的 ETF 份额 518880——采集路径待定）：不致命、
   无观测、告警留痕，保持既有静默语义但可观测。

   打桩方式：composite 与 akshare_macro 的 `import akshare as ak` 指向同一
   模块对象，直接在 akshare 模块属性上打桩即可同时覆盖两侧分支。"""
import logging

import akshare
import pandas as pd
import pytest

import backend.fetchers.akshare_macro as macro_mod
from backend.fetchers.base import Observation, build_fetcher

SRC = {"id": "copper", "name": "铜", "fetcher": "akshare.composite"}

COPPER_INSTS = [
    {"id": "cu-shfe-price", "code": "CU0", "metric": "price"},
    {"id": "cu-spot-price", "code": "CU", "metric": "price"},
    {"id": "cu-warrant", "code": "CU-WARRANT", "metric": "warehouse"},
    {"id": "cu-lme-stock", "code": "LME-CU-STOCK", "metric": "inventory"},
    {"id": "cu-lme-warrant", "code": "LME-CU-WARRANT", "metric": "warehouse"},
    {"id": "cu-lme-holding", "code": "LME-CU-POS", "metric": "positioning"},
]
GOLD_MACRO_INSTS = [
    {"id": "au-shfe-price", "code": "AU0", "metric": "price"},
    {"id": "au-cftc", "code": "CFTC-AU", "metric": "positioning"},
    {"id": "au-reserve", "code": "GOLD-RESERVE", "metric": "reserve"},
]

DAILY_DF = pd.DataFrame([
    {"date": "2026-10-16", "close": 886.48},
    {"date": "2026-10-15", "close": 890.0},
])
SPOT_DF = pd.DataFrame(
    [{"date": "20261015", "symbol": "CU", "spot_price": 110535.0}])
WARRANT_DATA = {"铜": pd.DataFrame([
    {"VARNAME": "铜", "WRTWGHTS": 34933.0, "ROWSTATUS": "0"},
])}
LME_STOCK_DF = pd.DataFrame([
    {"日期": "2026-07-17", "铜-库存": 296625.0, "铜-注册仓单": 130775.0},
    {"日期": "2026-07-16", "铜-库存": 297000.0, "铜-注册仓单": 131000.0},
])
LME_HOLDING_DF = pd.DataFrame([{"日期": "2021-09-03", "铜-净仓位": -3120.43}])
CFTC_DF = pd.DataFrame([{"日期": "2026-10-06", "黄金-净仓位": 210335.0}])
GOLD_RESERVE_DF = pd.DataFrame([
    {"统计时间": "2026.9", "黄金储备": 7747.0},
    {"统计时间": "2025.10", "黄金储备": 7466.0},
])


def stub_interfaces(monkeypatch, **overrides):
    """在共享 akshare 模块上打桩并带调用探针；值可为 DataFrame/dict/callable。"""
    defaults = {
        "futures_zh_daily_sina": DAILY_DF,
        "futures_spot_price": SPOT_DF,
        "futures_shfe_warehouse_receipt": WARRANT_DATA,
        "macro_euro_lme_stock": LME_STOCK_DF,
        "macro_euro_lme_holding": LME_HOLDING_DF,
        "macro_usa_cftc_c_holding": CFTC_DF,
        "macro_china_foreign_exchange_gold": GOLD_RESERVE_DF,
    }
    defaults.update(overrides)
    calls = {}
    for name, ret in defaults.items():
        probe = calls.setdefault(name, [])

        def make(ret=ret, probe=probe):
            def fn(*a, **k):
                probe.append((a, k))
                return ret(*a, **k) if callable(ret) else ret
            return fn

        monkeypatch.setattr(akshare, name, make())
    return calls


def build(monkeypatch, instruments, **overrides):
    calls = stub_interfaces(monkeypatch, **overrides)
    return build_fetcher("akshare.composite", SRC, instruments), calls


def keyed(obs):
    return {(o.instrument_id, o.trade_date, o.metric): o.value for o in obs}


# ---------- 铜源全量 instruments：各分支正确委托 + 合并 ----------

def test_copper_source_delegates_all_branches_and_merges(monkeypatch):
    f, calls = build(monkeypatch, COPPER_INSTS)
    obs = f.fetch("2026-10-15")
    got = keyed(obs)
    # 既有三支（SHFE 日K / 生意社现货 / 上期所仓单）不回归
    assert got["cu-shfe-price", "2026-10-16", "price"] == pytest.approx(886.48)
    assert got["cu-spot-price", "2026-10-15", "price"] == pytest.approx(110535.0)
    assert got["cu-warrant", "2026-10-15", "warehouse"] == pytest.approx(34933.0)
    # 宏观分支委托：LME 库存/仓单/净持仓
    assert got["cu-lme-stock", "2026-07-17", "inventory"] == pytest.approx(296625.0)
    assert got["cu-lme-warrant", "2026-07-17", "warehouse"] == pytest.approx(130775.0)
    assert got["cu-lme-holding", "2021-09-03", "positioning"] == pytest.approx(-3120.43)
    # 每接口恰调一次；LME 宽表一次调用同时供 STOCK+WAREHOUSE 两成分
    for name, n in [("futures_zh_daily_sina", 1), ("futures_spot_price", 1),
                    ("futures_shfe_warehouse_receipt", 1),
                    ("macro_euro_lme_stock", 1), ("macro_euro_lme_holding", 1)]:
        assert len(calls[name]) == n, name


def test_lme_warrant_routes_to_lme_not_shfe(monkeypatch):
    # LME 仓单走 lme_stock 宽表的注册仓单列；不得误入上期所仓单分支
    f, calls = build(monkeypatch, [COPPER_INSTS[4]])
    obs = f.fetch("2026-10-15")
    assert calls["futures_shfe_warehouse_receipt"] == []
    assert len(calls["macro_euro_lme_stock"]) == 1
    # 全历史语义：fixture 两行（07-17/07-16）各产一观测
    assert {(o.instrument_id, o.metric, o.value) for o in obs} == {
        ("cu-lme-warrant", "warehouse", 130775.0),
        ("cu-lme-warrant", "warehouse", 131000.0)}


# ---------- 金源宏观分支：CFTC-AU / GOLD-RESERVE 委托 ----------

def test_gold_macro_branches_delegated(monkeypatch):
    f, calls = build(monkeypatch, GOLD_MACRO_INSTS)
    obs = f.fetch("2026-10-15")
    got = keyed(obs)
    assert got["au-cftc", "2026-10-06", "positioning"] == pytest.approx(210335.0)
    assert got["au-reserve", "2026-09-30", "reserve"] == pytest.approx(7747.0)
    assert got["au-reserve", "2025-10-31", "reserve"] == pytest.approx(7466.0)
    assert got["au-shfe-price", "2026-10-16", "price"] == pytest.approx(886.48)
    assert len(calls["macro_usa_cftc_c_holding"]) == 1
    assert len(calls["macro_china_foreign_exchange_gold"]) == 1


# ---------- AU9999 / XAU：可选分支委托路由（类可注入） ----------

def _fake_spot_family(tag, built):
    """可选分支替身：构造时记 (tag, [codes]) 入共享 built 列表，fetch 产 999 观测。"""
    class _Fake:
        def __init__(self, source, instruments):
            self.rows = list(instruments)
            built.append((tag, [r["code"] for r in self.rows]))

        def fetch(self, trade_date):
            return [Observation(r["id"], "price", trade_date, 999.0)
                    for r in self.rows]

    return _Fake


def test_sge_and_london_delegated_when_classes_present(monkeypatch):
    built = []
    monkeypatch.setattr(macro_mod, "SgeSpotFetcher",
                        _fake_spot_family("sge", built), raising=False)
    monkeypatch.setattr(macro_mod, "LondonSpotFetcher",
                        _fake_spot_family("london", built), raising=False)
    insts = [{"id": "au-sge-price", "code": "AU9999", "metric": "price"},
             {"id": "au-london-price", "code": "XAU", "metric": "price"}]
    f, calls = build(monkeypatch, insts)
    obs = f.fetch("2026-10-15")
    # 各自只收到本族子集，委托后观测合并返回
    assert built == [("sge", ["AU9999"]), ("london", ["XAU"])]
    assert {(o.instrument_id, o.value) for o in obs} == {
        ("au-sge-price", 999.0), ("au-london-price", 999.0)}
    # 无其他成分 → 既有七接口全未触（分支短路）
    assert all(not p for p in calls.values())


def test_missing_optional_class_warns_and_skips(monkeypatch, caplog):
    # 类未落地（并行任务负责）：跳过该分支 + warning，不 fail；其余分支照常
    monkeypatch.delattr(macro_mod, "SgeSpotFetcher", raising=False)
    monkeypatch.delattr(macro_mod, "LondonSpotFetcher", raising=False)
    insts = [{"id": "au-shfe-price", "code": "AU0", "metric": "price"},
             {"id": "au-london-price", "code": "XAU", "metric": "price"}]
    f, calls = build(monkeypatch, insts)
    obs = f.fetch("2026-10-15")
    assert {o.instrument_id for o in obs} == {"au-shfe-price"}
    assert "LondonSpotFetcher" in caplog.text


# ---------- 原子性 / 短路 / 未识别 code ----------

def test_macro_branch_failure_propagates(monkeypatch):
    # 源级原子：任一分支异常整体抛，而非静默返回半份数据
    def boom():
        raise RuntimeError("lme down")

    f, _ = build(monkeypatch, [COPPER_INSTS[3]], macro_euro_lme_stock=boom)
    with pytest.raises(RuntimeError):
        f.fetch("2026-10-15")


def test_macro_only_source_short_circuits_other_interfaces(monkeypatch):
    # 只挂 GOLD-RESERVE：其余六个接口一个都不触
    f, calls = build(monkeypatch,
                     [{"id": "au-reserve", "code": "GOLD-RESERVE",
                       "metric": "reserve"}])
    obs = f.fetch("2026-10-15")
    assert len(calls["macro_china_foreign_exchange_gold"]) == 1
    for name in ("futures_zh_daily_sina", "futures_spot_price",
                 "futures_shfe_warehouse_receipt", "macro_euro_lme_stock",
                 "macro_euro_lme_holding", "macro_usa_cftc_c_holding"):
        assert calls[name] == [], name
    assert keyed(obs) == {("au-reserve", "2026-09-30", "reserve"): 7747.0,
                          ("au-reserve", "2025-10-31", "reserve"): 7466.0}


def test_unmatched_code_warns_and_yields_nothing(monkeypatch, caplog):
    # 未识别 code（如 gold 源上的 ETF 份额 518880，采集路径待定）：
    # 不致命、无观测、告警留痕
    f, calls = build(monkeypatch,
                     [{"id": "518880", "code": "518880",
                       "metric": "holding"}])
    assert f.fetch("2026-10-15") == []
    assert all(not p for p in calls.values())   # 不触任何接口
    assert "518880" in caplog.text
