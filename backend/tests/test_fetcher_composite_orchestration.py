"""composite 编排化（v1.2 增量）单测：按 code 家族把宏观/贵金属分支委托给
   backend/fetchers/akshare_macro.py 的既有子 fetcher：

   - LME-{CU,AL}-{STOCK,WARRANT} → LmeStockFetcher（同一宽表接口一次调用
     同时供库存 inventory 与注册仓单 warehouse 两类成分）
   - LME-{CU,AL}-POS             → LmeHoldingFetcher（positioning）
   - CFTC-AU                     → CftcNcFetcher（positioning，周频）
   - GOLD-RESERVE                → GoldReserveFetcher（reserve，月频锚月末）
   - AU9999 → SgeSpotFetcher、XAU → LondonSpotFetcher：现货分支（可注入，
     类缺失时 log.warning 跳过、不 fail）
   - 小写 'cu'/'al'/'ao' → FuturesInventoryFetcher（东财库存，metric='inventory'；
     回归修复：cu-inventory 迁入铜组后由此分支续供，接口仅留近 60 日）
   - 6 位数字 code → EtfScaleFetcher（上交所份额快照；metric 'share' 重映射为
     instruments 行的 'holding'——黄金 ETF 持仓=份额，语义等价）

   契约（v1.2.1 契约变更：源级原子 → 分支级失败隔离）：各分支观测合并返回；
   单分支异常 → log.warning（分支名+错误摘要）跳过，其余分支照常产出
   （部分可用数据不再被单所断连拖死）；全部已执行分支都失败才抛
   （run_source 仍可标 failed）；无成分的分支短路、不触接口；全部分支空 → []。
   未识别 code（家族表之外的任意串）：不致命、无观测、告警留痕，
   保持既有静默语义但可观测。

   打桩方式：composite 与 akshare_macro 的 `import akshare as ak` 指向同一
   模块对象，直接在 akshare 模块属性上打桩即可同时覆盖两侧分支。

   另含 seed↔composite↔子 fetcher 三方一致性集成测试（v1.2 综合审查 Minor 修复）：
   解析 backend/seed.sql 真实内容，逐一断言商品组源全部品种的路由家族、
   硬编码分支产出 metric 与 seed 行一致、composite 接线与各源子 fetcher 齐备。"""
import logging
import re
from pathlib import Path

import akshare
import pandas as pd
import pytest

import backend.fetchers.akshare_macro as macro_mod
from backend.fetchers import akshare_composite as comp_mod
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
INVENTORY_DF = pd.DataFrame([
    {"日期": "2026-10-15", "库存": 65421.0, "增减": -591.0},
    {"日期": "2026-10-14", "库存": 66012.0, "增减": 120.0},
])
ETF_SNAPSHOT_DF = pd.DataFrame([
    {"代码": "518880", "基金简称": "华安黄金ETF", "份额": 8930000000.0},
    {"代码": "518800", "基金简称": "国泰黄金ETF", "份额": 2200000000.0},
    {"代码": "518660", "基金简称": "博时黄金ETF", "份额": 1500000000.0},
    {"代码": "510300", "基金简称": "沪深300ETF", "份额": 90000000000.0},
])
GOLD_ETF_INSTS = [
    {"id": "au-etf-518880", "code": "518880", "metric": "holding"},
    {"id": "au-etf-518800", "code": "518800", "metric": "holding"},
    {"id": "au-etf-518660", "code": "518660", "metric": "holding"},
]


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
        "futures_inventory_em": INVENTORY_DF,
        "fund_etf_scale_sse": ETF_SNAPSHOT_DF,
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
    # 无其他成分 → 打桩接口全未触（分支短路）
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


# ---------- 库存分支（回归修复）+ ETF 持仓分支（metric 重映射） ----------

def test_copper_inventory_branch_delegated_and_merged(monkeypatch):
    # 回归修复：cu-inventory 迁入铜组后 code 'cu' 曾无分支断更——委托东财库存
    insts = COPPER_INSTS + [{"id": "cu-inventory", "code": "cu",
                             "metric": "inventory"}]
    f, calls = build(monkeypatch, insts)
    obs = f.fetch("2026-10-15")
    got = keyed(obs)
    assert got["cu-inventory", "2026-10-15", "inventory"] == pytest.approx(65421.0)
    assert got["cu-inventory", "2026-10-14", "inventory"] == pytest.approx(66012.0)
    # 既有分支不回归
    assert got["cu-shfe-price", "2026-10-16", "price"] == pytest.approx(886.48)
    assert got["cu-lme-stock", "2026-07-17", "inventory"] == pytest.approx(296625.0)
    # em 接口以 instruments 行 code 作 symbol，恰调一次
    assert calls["futures_inventory_em"] == [((), {"symbol": "cu"})]


def test_al_ao_inventory_routes_same_branch(monkeypatch):
    al = [{"id": "al-shfe-price", "code": "AL0", "metric": "price"},
          {"id": "al-inventory", "code": "al", "metric": "inventory"}]
    f, calls = build(monkeypatch, al)
    got = keyed(f.fetch("2026-10-15"))
    assert got["al-inventory", "2026-10-15", "inventory"] == pytest.approx(65421.0)
    assert got["al-shfe-price", "2026-10-16", "price"] == pytest.approx(886.48)
    assert calls["futures_inventory_em"] == [((), {"symbol": "al"})]


def test_etf_holding_branch_metric_remapped(monkeypatch):
    # ETF 份额快照：6 位数字 code 走 EtfScaleFetcher 上交所分支，
    # 产出 metric 'share' 重映射为 instruments 行的 'holding'
    insts = [{"id": "au-shfe-price", "code": "AU0", "metric": "price"}] \
        + GOLD_ETF_INSTS
    f, calls = build(monkeypatch, insts, fund_scale_daily_szse=pd.DataFrame())
    obs = f.fetch("2026-10-15")
    got = keyed(obs)
    assert got["au-etf-518880", "2026-10-15", "holding"] == pytest.approx(89.3)
    assert got["au-etf-518800", "2026-10-15", "holding"] == pytest.approx(22.0)
    assert got["au-etf-518660", "2026-10-15", "holding"] == pytest.approx(15.0)
    assert all(o.metric == "holding" for o in obs if o.instrument_id.startswith("au-etf"))
    # 份额→亿份 ÷1e8；基金简称 raw 元数据随观测保留
    raw = {o.instrument_id: o.raw for o in obs if o.instrument_id.startswith("au-etf")}
    assert raw["au-etf-518880"] == {"name": "华安黄金ETF"}
    # 快照中未注册行（510300）被过滤；深交所分支未触
    assert len(calls["fund_etf_scale_sse"]) == 1
    assert calls["fund_scale_daily_szse"] == []
    # 同源其他分支不回归
    assert got["au-shfe-price", "2026-10-16", "price"] == pytest.approx(886.48)


def test_inventory_branch_failure_isolated(monkeypatch, caplog):
    # v1.2.1 契约变更（源级原子 → 分支级失败隔离）：库存分支挂 → 告警跳过，
    # SHFE 日K 等其余分支照常产出（部分可用数据不再被单接口故障拖死）
    def boom(*a, **k):
        raise RuntimeError("em down")

    f, _ = build(monkeypatch,
                 [{"id": "cu-inventory", "code": "cu", "metric": "inventory"},
                  COPPER_INSTS[0]],
                 futures_inventory_em=boom)
    got = keyed(f.fetch("2026-10-15"))
    assert got["cu-shfe-price", "2026-10-16", "price"] == pytest.approx(886.48)
    assert all(k[0] != "cu-inventory" for k in got)
    assert "FuturesInventoryFetcher" in caplog.text and "em down" in caplog.text


# ---------- 分支级失败隔离 / 短路 / 未识别 code ----------

def test_macro_branch_failure_isolated(monkeypatch, caplog):
    # v1.2.1 契约变更：LME 分支挂 → 告警跳过，其余分支照常产出
    def boom(*a, **k):
        raise RuntimeError("lme down")

    f, _ = build(monkeypatch, [COPPER_INSTS[3], COPPER_INSTS[0]],
                 macro_euro_lme_stock=boom)
    got = keyed(f.fetch("2026-10-15"))
    assert got["cu-shfe-price", "2026-10-16", "price"] == pytest.approx(886.48)
    assert all(k[0] != "cu-lme-stock" for k in got)
    assert "LmeStockFetcher" in caplog.text and "lme down" in caplog.text


def test_shfe_branch_failure_isolated(monkeypatch, caplog):
    # SHFE 内部分支同样隔离：日K 挂 → 库存分支照常产出
    def boom(*a, **k):
        raise RuntimeError("sina down")

    f, _ = build(monkeypatch,
                 [COPPER_INSTS[0],
                  {"id": "cu-inventory", "code": "cu", "metric": "inventory"}],
                 futures_zh_daily_sina=boom)
    got = keyed(f.fetch("2026-10-15"))
    assert got["cu-inventory", "2026-10-15", "inventory"] == pytest.approx(65421.0)
    assert all(k[0] != "cu-shfe-price" for k in got)
    assert "shfe_daily" in caplog.text and "sina down" in caplog.text


def test_sse_branch_survives_szse_failure(monkeypatch, caplog):
    # 触发场景（v1.2.1）：沪深混合源深交所断连 3+ 小时——上交所品种观测
    # 必须照常产出，不被单所断连拖死（159845 走深交所、518880 走上交所）
    def boom(*a, **k):
        raise RuntimeError("szse down")

    f, _ = build(monkeypatch,
                 [{"id": "au-etf-518880", "code": "518880", "metric": "holding"},
                  {"id": "test-159845", "code": "159845", "metric": "holding"}],
                 fund_scale_daily_szse=boom)
    got = keyed(f.fetch("2026-10-15"))
    assert got["au-etf-518880", "2026-10-15", "holding"] == pytest.approx(89.3)
    assert all(k[0] != "test-159845" for k in got)
    assert "SZSE" in caplog.text and "szse down" in caplog.text


def test_all_branches_fail_reraises(monkeypatch):
    # 全部已执行分支都失败 → 仍抛（run_source 能标 failed；静默全空更危险）
    def boom(*a, **k):
        raise RuntimeError("all down")

    f, _ = build(monkeypatch,
                 [COPPER_INSTS[0],
                  {"id": "cu-inventory", "code": "cu", "metric": "inventory"},
                  COPPER_INSTS[3]],
                 futures_zh_daily_sina=boom, futures_inventory_em=boom,
                 macro_euro_lme_stock=boom)
    with pytest.raises(RuntimeError, match="全部分支失败"):
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
    # 未识别 code（家族表之外的任意串）：不致命、无观测、告警留痕
    f, calls = build(monkeypatch,
                     [{"id": "weird-inst", "code": "XX-UNKNOWN",
                       "metric": "price"}])
    assert f.fetch("2026-10-15") == []
    assert all(not p for p in calls.values())   # 不触任何接口
    assert "XX-UNKNOWN" in caplog.text


# ---------- seed ↔ composite ↔ 子 fetcher 三方一致性（v1.2 综合审查 Minor 修复） ----------

_SEED_SQL = Path(__file__).resolve().parents[1] / "seed.sql"
_SQL_VALUES_RE = re.compile(
    r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES(.*?);",
    re.S | re.I)

# 全部 27 个品种的预期路由（与 seed.sql 商品组源 instruments 一一对应）。
# 双向集合比对：seed 增删品种必须同步更新此表——锁死“seed↔composite↔
# 子 fetcher”这条最容易断的缝（上次 seed 对齐改的就是它）。
EXPECTED_ROUTE = {
    # copper：SHFE 日K / 生意社现货 / 上期所仓单 / LME 库存·仓单·净持仓 / 东财库存
    "CU0": "shfe_daily",
    "CU": "shfe_spot",
    "CU-WARRANT": "shfe_warrant",
    "LME-CU-STOCK": "lme_stock",
    "LME-CU-WARRANT": "lme_stock",
    "LME-CU-POS": "lme_holding",
    "cu": "inventory",
    # aluminum
    "AL0": "shfe_daily",
    "AL": "shfe_spot",
    "al": "inventory",
    "AL-WARRANT": "shfe_warrant",
    "LME-AL-STOCK": "lme_stock",
    "LME-AL-WARRANT": "lme_stock",
    "LME-AL-POS": "lme_holding",
    # alumina
    "AO0": "shfe_daily",
    "AO": "shfe_spot",
    "ao": "inventory",
    "AO-WARRANT": "shfe_warrant",
    # gold：SHFE 日K / SGE 现货 / 仓单 / 央行储备 / ETF 持仓 / CFTC / 伦敦金现
    "AU0": "shfe_daily",
    "AU9999": "sge_spot",
    "AU-WARRANT": "shfe_warrant",
    "GOLD-RESERVE": "reserve",
    "518880": "etf",
    "518800": "etf",
    "518660": "etf",
    "CFTC-AU": "cftc",
    "XAU": "london_spot",
}

# 子 fetcher 硬编码产出 metric 的分支：必须与 seed 行 metric 一致。
# （SHFE 三支与 ETF 重映射为行驱动——composite 用 targets 里的 seed metric，
# 接线正确性由下方 targets 断言覆盖，无需在此重复。）
BRANCH_METRIC = {
    "cu": "inventory", "al": "inventory", "ao": "inventory",
    "LME-CU-STOCK": "inventory", "LME-CU-WARRANT": "warehouse",
    "LME-AL-STOCK": "inventory", "LME-AL-WARRANT": "warehouse",
    "LME-CU-POS": "positioning", "LME-AL-POS": "positioning",
    "GOLD-RESERVE": "reserve", "CFTC-AU": "positioning",
    "AU9999": "price", "XAU": "price",
}

# 各 composite 源构建期应接线的委托子 fetcher 类型（缺一即该分支断供）
EXPECTED_SUBS = {
    "copper": ["LmeStockFetcher", "LmeHoldingFetcher", "FuturesInventoryFetcher"],
    "aluminum": ["LmeStockFetcher", "LmeHoldingFetcher", "FuturesInventoryFetcher"],
    "alumina": ["FuturesInventoryFetcher"],
    "gold": ["SgeSpotFetcher", "GoldReserveFetcher", "CftcNcFetcher",
             "LondonSpotFetcher", "_HoldingEtfFetcher"],
}


def _sql_token(tok: str):
    tok = tok.strip()
    if tok.upper() == "NULL":
        return None
    if len(tok) >= 2 and tok[0] == "'" and tok[-1] == "'":
        return tok[1:-1].replace("''", "'")     # SQL 单引号转义
    return tok


def _sql_split(s: str) -> list[str]:
    """按顶层逗号切分（单引号字符串内的逗号与 '' 转义不切断）。"""
    parts, buf, in_q, i = [], [], False, 0
    while i < len(s):
        ch = s[i]
        if in_q:
            if ch == "'":
                if s[i + 1:i + 2] == "'":
                    buf.append("''")
                    i += 2
                    continue
                in_q = False
            buf.append(ch)
        elif ch == "'":
            in_q = True
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def _sql_tuples(body: str) -> list[list[str]]:
    """VALUES 体 → 逐元组字段列表（括号配对、引号感知）。"""
    out, depth, buf, in_q, i = [], 0, [], False, 0
    while i < len(body):
        ch = body[i]
        if in_q:
            if ch == "'":
                if body[i + 1:i + 2] == "'":
                    buf.append("''")
                    i += 2
                    continue
                in_q = False
            buf.append(ch)
        elif ch == "'":
            in_q = True
            buf.append(ch)
        elif ch == "(":
            depth += 1
            if depth == 1:
                buf = []
        elif ch == ")":
            depth -= 1
            if depth == 0:
                out.append([_sql_token(t) for t in _sql_split("".join(buf))])
                buf = []
            else:
                buf.append(ch)
        elif depth > 0:
            buf.append(ch)
        i += 1
    return out


def _parse_seed():
    """解析 seed.sql 的 sources/instruments INSERT：按语句列头映射（容忍列序
    变化与 v1/v1.2 两代列差异、INSERT OR IGNORE 拼写、ON CONFLICT 子句），
    id 冲突取最后一条（对齐 cu-inventory 行的 INSERT ... ON CONFLICT DO UPDATE）。"""
    sources, insts = {}, {}
    for m in _SQL_VALUES_RE.finditer(_SEED_SQL.read_text(encoding="utf-8")):
        table = m.group(1).lower()
        cols = [c.strip().strip('"').lower() for c in m.group(2).split(",")]
        if table not in ("sources", "instruments"):
            continue
        for fields in _sql_tuples(m.group(3)):
            if len(fields) != len(cols):
                continue    # ON CONFLICT(id) 等非 VALUES 元组
            row = dict(zip(cols, fields))
            (sources if table == "sources" else insts)[row["id"]] = row
    return sources, insts


def _route(code) -> str | None:
    """composite 路由镜像：_family() 之上把 SHFE 家族细分为三支（用模块公开
    常量推导，与 fetch() 内分流同一判据）；可选现货类名映射为可读家族名。"""
    if code in comp_mod.SHFE_DAILY_CODES:
        return "shfe_daily"
    if code in comp_mod.SPOT_VARS:
        return "shfe_spot"
    if code.endswith("-WARRANT") and not code.startswith("LME-"):
        return "shfe_warrant"
    fam = comp_mod._family(code)
    if fam is None:
        return None
    return {"SgeSpotFetcher": "sge_spot",
            "LondonSpotFetcher": "london_spot"}.get(fam, fam)


def test_seed_composite_route_consistency():
    """seed.sql ↔ composite 路由 ↔ 子 fetcher 三方一致性集成锁：
       解析 seed.sql 真实内容里 akshare.composite 源的全部 instruments，
       逐一断言 27 个品种的 code 路由到预期家族、硬编码分支产出 metric 与
       seed 行一致、composite targets 按 (id, metric) 与 seed 对齐、
       各源构建期子 fetcher 齐备。"""
    sources, insts = _parse_seed()
    # 商品组四源全部走 akshare.composite（新增 composite 源须同步更新断言与路由表）
    comp_ids = {sid for sid, s in sources.items()
                if s["fetcher"] == "akshare.composite"}
    assert comp_ids == {"copper", "aluminum", "alumina", "gold"}
    rows = [r for r in insts.values() if r["source_id"] in comp_ids]
    assert len(rows) == 27
    codes = [r["code"] for r in rows]
    assert len(set(codes)) == len(codes)        # code 唯一（composite targets 键）
    # 双向覆盖：seed 增删品种 vs EXPECTED_ROUTE 必须同步，防静默漂移
    assert set(codes) == set(EXPECTED_ROUTE)

    for r in rows:
        code, metric = r["code"], r["metric"]
        route = _route(code)
        assert route == EXPECTED_ROUTE[code], (
            f"{code}: 路由 {route!r} != 预期 {EXPECTED_ROUTE[code]!r}")
        if code in BRANCH_METRIC:
            assert metric == BRANCH_METRIC[code], (
                f"{code}: seed metric {metric!r} != 分支硬编码产出 "
                f"{BRANCH_METRIC[code]!r}")

    # 构建期接线：每源可构建，targets 把 code → (id, metric) 与 seed 行对齐，
    # 委托子 fetcher 类型齐备（构建不触接口，无需打桩）
    for sid in sorted(comp_ids):
        inst_rows = sorted((r for r in rows if r["source_id"] == sid),
                           key=lambda r: int(r["sort_order"]))
        payload = [{"id": r["id"], "code": r["code"], "metric": r["metric"]}
                   for r in inst_rows]
        f = build_fetcher("akshare.composite", {"id": sid}, payload)
        assert isinstance(f, comp_mod.CommodityCompositeFetcher)
        assert set(f.targets) == {r["code"] for r in inst_rows}
        for r in inst_rows:
            assert f.targets[r["code"]] == (r["id"], r["metric"]), r["code"]
        subs = sorted(type(s).__name__ for s in f._subs)
        assert subs == sorted(EXPECTED_SUBS[sid]), f"{sid}: 子 fetcher {subs}"
