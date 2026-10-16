"""宏观全历史型 fetcher 单测（v1.2 Task C）——fixture 回放，离线。
   覆盖：LME 库存/仓单（akshare.lme_stock）、LME 净持仓（akshare.lme_holding）、
   CFTC 商品非商业净多（akshare.cftc_nc）、央行黄金储备（akshare.gold_reserve）。
   共同语义：全历史型——fetch 忽略 trade_date 返回接口全量观测，幂等 upsert；
   backfill 首日全量入库后 covered=True 自然续跑。
   instrument code 约定（与 Task A seed 对齐，id=code 同名，fetcher 只消费 id/code）：
   LME-{CU|AL}-{STOCK|WARRANT|POS}、CFTC-AU、GOLD-RESERVE。
   实测注记（2026-10-16）：任务书预期 macro_usa_cftc_nc_holding 实为【外汇】非商业
   （无商品列）；商品非商业在 macro_usa_cftc_c_holding，且四接口均无铜品种——
   CFTC-CU 无法由 akshare 供给，fetcher 对其构建期 fail-loud（见对应测试）。"""
import calendar
import types
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

import backend.fetchers  # noqa: F401 触发注册
from backend.fetchers.base import build_fetcher

FIX = Path(__file__).parent / "fixtures"

# read_json 会把 'YYYY.M' 月标数值推断成 float（"2025.10"→2025.1，十月丢失），
# 与 akshare 真实返回（str）不符，故 gold fixture 显式 dtype=str（同 etf 代码列先例）
LME_STOCK = pd.read_json(FIX / "macro_lme_stock.json")
LME_HOLDING = pd.read_json(FIX / "macro_lme_holding.json")
CFTC = pd.read_json(FIX / "macro_cftc_nc.json")
GOLD = pd.read_json(FIX / "macro_gold_reserve.json", dtype={"统计时间": str})

LME_STOCK_INSTS = [
    {"id": "LME-CU-STOCK", "code": "LME-CU-STOCK", "metric": "inventory"},
    {"id": "LME-CU-WARRANT", "code": "LME-CU-WARRANT", "metric": "warehouse"},
    {"id": "LME-AL-STOCK", "code": "LME-AL-STOCK", "metric": "inventory"},
    {"id": "LME-AL-WARRANT", "code": "LME-AL-WARRANT", "metric": "warehouse"},
]
LME_HOLDING_INSTS = [
    {"id": "LME-CU-POS", "code": "LME-CU-POS", "metric": "positioning"},
    {"id": "LME-AL-POS", "code": "LME-AL-POS", "metric": "positioning"},
]
CFTC_INSTS = [
    {"id": "CFTC-AU", "code": "CFTC-AU", "metric": "positioning"},
]
GOLD_INSTS = [
    {"id": "GOLD-RESERVE", "code": "GOLD-RESERVE", "metric": "reserve"},
]

# (注册名, instruments, akshare 接口名, 回放 fixture)——四 fetcher 共性测试参数化
CASES = [
    ("akshare.lme_stock", LME_STOCK_INSTS, "macro_euro_lme_stock", LME_STOCK),
    ("akshare.lme_holding", LME_HOLDING_INSTS, "macro_euro_lme_holding", LME_HOLDING),
    ("akshare.cftc_nc", CFTC_INSTS, "macro_usa_cftc_c_holding", CFTC),
    ("akshare.gold_reserve", GOLD_INSTS, "macro_china_foreign_exchange_gold", GOLD),
]


def make(monkeypatch, name, instruments, **fns):
    """按注册名构建 fetcher，akshare 接口桩回放（桩参数名与生产调用对齐）。"""
    import backend.fetchers.akshare_macro as mod
    monkeypatch.setattr(mod, "ak", types.SimpleNamespace(**fns))
    return build_fetcher(name, {"id": "test-src"}, instruments)


def by_key(obs):
    """(instrument_id, trade_date) -> value，便于与 fixture 行对账。"""
    return {(o.instrument_id, o.trade_date): o.value for o in obs}


def fixture_kv(df, col, iid):
    """fixture 列 -> {(iid, 'YYYY-MM-DD'): value}（NaN 行不产观测，与 fetcher 同约定）。"""
    return {(iid, str(d)[:10]): float(v)
            for d, v in zip(df["日期"], df[col]) if pd.notna(v)}


# ---------- LME 库存/仓单（akshare.lme_stock） ----------

def test_lme_stock_four_series(monkeypatch):
    f = make(monkeypatch, "akshare.lme_stock", LME_STOCK_INSTS,
             macro_euro_lme_stock=lambda: LME_STOCK)
    obs = f.fetch("2026-10-16")
    expected = (fixture_kv(LME_STOCK, "铜-库存", "LME-CU-STOCK")
                | fixture_kv(LME_STOCK, "铜-注册仓单", "LME-CU-WARRANT")
                | fixture_kv(LME_STOCK, "铝-库存", "LME-AL-STOCK")
                | fixture_kv(LME_STOCK, "铝-注册仓单", "LME-AL-WARRANT"))
    assert by_key(obs) == expected          # 全量行级对账
    assert len(obs) == len(expected)
    m = {o.instrument_id: o.metric for o in obs}
    assert m == {"LME-CU-STOCK": "inventory", "LME-CU-WARRANT": "warehouse",
                 "LME-AL-STOCK": "inventory", "LME-AL-WARRANT": "warehouse"}

def test_lme_stock_nan_skipped_by_count(monkeypatch):
    # 注册仓单列 fixture 含 NaN 行——逐列计数 = 非空行数（NaN 不产观测不报错）
    f = make(monkeypatch, "akshare.lme_stock", LME_STOCK_INSTS,
             macro_euro_lme_stock=lambda: LME_STOCK)
    cnt = Counter(o.instrument_id for o in f.fetch("2026-10-16"))
    for iid, colname in (("LME-CU-STOCK", "铜-库存"), ("LME-CU-WARRANT", "铜-注册仓单"),
                         ("LME-AL-STOCK", "铝-库存"), ("LME-AL-WARRANT", "铝-注册仓单")):
        assert cnt[iid] == int(LME_STOCK[colname].notna().sum())

def test_lme_stock_only_registered_instruments(monkeypatch):
    # 接口含镍/锌/铅/锡与注销仓单列——未注册的仪器不得产出
    f = make(monkeypatch, "akshare.lme_stock", LME_STOCK_INSTS,
             macro_euro_lme_stock=lambda: LME_STOCK)
    assert {o.instrument_id for o in f.fetch("2026-10-16")} == \
        {i["id"] for i in LME_STOCK_INSTS}

def test_lme_stock_dates_normalized(monkeypatch):
    f = make(monkeypatch, "akshare.lme_stock", LME_STOCK_INSTS,
             macro_euro_lme_stock=lambda: LME_STOCK)
    obs = f.fetch("2026-10-16")
    assert all(len(o.trade_date) == 10 and o.trade_date[4] == "-" for o in obs)
    assert min(o.trade_date for o in obs) == min(str(d) for d in LME_STOCK["日期"])

def test_lme_stock_timestamp_dates(monkeypatch):
    # 真实接口日期形态 str/Timestamp 二选一（cu fixture 已覆盖 epoch-ms）——Timestamp 同样归一
    df = pd.DataFrame([{"日期": pd.Timestamp("2026-07-17"), "铜-库存": 296625.0,
                        "铜-注册仓单": 130775.0, "铝-库存": 280100.0,
                        "铝-注册仓单": 245900.0}])
    f = make(monkeypatch, "akshare.lme_stock", LME_STOCK_INSTS,
             macro_euro_lme_stock=lambda: df)
    assert all(o.trade_date == "2026-07-17"
               for o in f.fetch("2026-10-16"))

def test_lme_stock_unknown_code_fails_loud():
    # seed 拼错 code 在构建期暴露（fail-loud），而非静默丢数据
    with pytest.raises(ValueError, match="LME-ZN-STOCK"):
        build_fetcher("akshare.lme_stock", {"id": "test-src"},
                      [{"id": "LME-ZN-STOCK", "code": "LME-ZN-STOCK"}])

# ---------- LME 净持仓（akshare.lme_holding） ----------

def test_lme_holding_net_positions(monkeypatch):
    f = make(monkeypatch, "akshare.lme_holding", LME_HOLDING_INSTS,
             macro_euro_lme_holding=lambda: LME_HOLDING)
    obs = f.fetch("2026-10-16")
    expected = (fixture_kv(LME_HOLDING, "铜-净仓位", "LME-CU-POS")
                | fixture_kv(LME_HOLDING, "铝-净仓位", "LME-AL-POS"))
    assert by_key(obs) == expected          # 只取净仓位；多头/空头列不产出
    assert len(obs) == len(expected)
    assert all(o.metric == "positioning" for o in obs)
    # 停更尾行对账（金十源 2021-09-03 后停更）
    assert by_key(obs)[("LME-CU-POS", "2021-09-03")] == -3120.43
    assert by_key(obs)[("LME-AL-POS", "2021-09-03")] == -6754.82

# ---------- CFTC 商品非商业净多（akshare.cftc_nc） ----------

def test_cftc_gold_net(monkeypatch):
    f = make(monkeypatch, "akshare.cftc_nc", CFTC_INSTS,
             macro_usa_cftc_c_holding=lambda: CFTC)
    obs = f.fetch("2026-10-16")
    expected = fixture_kv(CFTC, "黄金-净仓位", "CFTC-AU")
    assert by_key(obs) == expected
    assert len(obs) == len(expected)
    assert all(o.metric == "positioning" for o in obs)

def test_cftc_copper_unsupported_fails_loud():
    # akshare 四个 CFTC 接口均无铜品种列（金十商品非商业仅 12 商品，见模块 docstring）——
    # CFTC-CU 构建期即报错并说明出路，而非每日静默 empty
    with pytest.raises(ValueError, match="铜"):
        build_fetcher("akshare.cftc_nc", {"id": "test-src"},
                      [{"id": "CFTC-CU", "code": "CFTC-CU"}])

# ---------- 央行黄金储备（akshare.gold_reserve） ----------

def test_gold_reserve_month_end_dates(monkeypatch):
    f = make(monkeypatch, "akshare.gold_reserve", GOLD_INSTS,
             macro_china_foreign_exchange_gold=lambda: GOLD)
    obs = f.fetch("2026-10-16")
    assert all(o.metric == "reserve" for o in obs)
    assert len(obs) == int(GOLD["黄金储备"].notna().sum())
    first, last = min(obs, key=lambda o: o.trade_date), max(obs, key=lambda o: o.trade_date)
    # 'YYYY.M' 归一为当月最后一日（月度观测锚定月末；接口只给月粒度）
    assert (first.trade_date, first.value) == ("1978-12-31", 1280.0)
    assert (last.trade_date, last.value) == ("2026-09-30", 7747.0)
    for o in obs:   # 每个日期都是当月最后一日（含闰年/大小月）
        y, m, d = map(int, o.trade_date.split("-"))
        assert d == calendar.monthrange(y, m)[1]

def test_gold_reserve_bad_month_fails_loud(monkeypatch):
    # 结构性日期异常（如月份越界/格式变更）fail-loud，而非静默跳过
    df = pd.DataFrame([{"统计时间": "2026.9", "黄金储备": 7747.0},
                       {"统计时间": "2026.13", "黄金储备": 7800.0}])
    f = make(monkeypatch, "akshare.gold_reserve", GOLD_INSTS,
             macro_china_foreign_exchange_gold=lambda: df)
    with pytest.raises(ValueError, match="统计时间"):
        f.fetch("2026-10-16")

def test_gold_reserve_nan_date_skipped(monkeypatch):
    df = pd.DataFrame([{"统计时间": "2026.9", "黄金储备": 7747.0},
                       {"统计时间": None, "黄金储备": 7800.0}])
    f = make(monkeypatch, "akshare.gold_reserve", GOLD_INSTS,
             macro_china_foreign_exchange_gold=lambda: df)
    obs = f.fetch("2026-10-16")
    assert len(obs) == 1 and obs[0].trade_date == "2026-09-30"

def test_gold_reserve_unknown_code_fails_loud():
    with pytest.raises(ValueError, match="GOLD-XXX"):
        build_fetcher("akshare.gold_reserve", {"id": "test-src"},
                      [{"id": "GOLD-XXX", "code": "GOLD-XXX"}])

# ---------- 四 fetcher 共性语义（参数化） ----------

@pytest.mark.parametrize("name,insts,fn,df", CASES, ids=[c[0] for c in CASES])
def test_ignores_trade_date(monkeypatch, name, insts, fn, df):
    # 全历史型：传任意日期返回相同全量（幂等 upsert 的前提）
    f = make(monkeypatch, name, insts, **{fn: lambda: df})
    assert f.fetch("2000-01-01") == f.fetch("2026-10-16")

@pytest.mark.parametrize("name,insts,fn,_df", CASES, ids=[c[0] for c in CASES])
def test_empty_returns_empty(monkeypatch, name, insts, fn, _df):
    # 生产路径：接口波动返回 None/空表 → []（由 collect 判 empty 告警，fetch 不炸）
    for empty in (None, pd.DataFrame()):
        f = make(monkeypatch, name, insts, **{fn: lambda: empty})
        assert f.fetch("2026-10-16") == []

@pytest.mark.parametrize("name,insts,fn,df,drop", [
    (*c, drop) for c, drop in zip(CASES, ["铜-库存", "铜-净仓位", "黄金-净仓位", "黄金储备"])
], ids=[c[0] for c in CASES])
def test_missing_column_raises(monkeypatch, name, insts, fn, df, drop):
    # 缺列 fail-loud（禁静默产出错误数据）——akshare 字段改名在此暴露
    f = make(monkeypatch, name, insts,
             **{fn: lambda: df.drop(columns=[drop])})
    with pytest.raises(ValueError):
        f.fetch("2026-10-16")
