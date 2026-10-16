"""商品复合 fetcher（akshare.composite）单测：SHFE 日K / 生意社现货 / 上期所仓单。
   fixture 由 gen_commodity_fixtures.py 录制（2026-10-15 实测）：
   - 日K：futures_zh_daily_sina('AU0') 全史 4570 行截尾近 500 行；date 列 str/Timestamp
     双形态均由 normalize_date 覆盖
   - 现货：futures_spot_price 生意社（100ppi）已下架氧化铝——fixture 如实缺 AO 行；
     fetcher 对缺品种按"未注册成分"语义跳过（恢复后自动接入，AO 映射另有合成样本覆盖）
   - 仓单：dict[品种] = DataFrame，列 [VARNAME, REGNAME, WHABBRNAME, WRTWGHTS,
     WRTCHANGE, ROWSTATUS]；ROWSTATUS 实测为字符串（'0'明细/'1'区域合计/'2'总计）；
     WRTWGHTS 明细行求和与交易所"总计"行核对一致；单位铜/铝/氧化铝=吨、黄金=千克
     （单位语义由 seed instruments.unit 承载，fetcher 透传原值）"""
import json
import re
import types
from pathlib import Path

import pandas as pd
import pytest

from backend.fetchers._util import normalize_date
from backend.fetchers.base import build_fetcher

FIX = Path(__file__).parent / "fixtures"
FIX_DAILY = FIX / "commodity_shfe_daily_au0.json"
FIX_SPOT = FIX / "commodity_spot_price.json"
FIX_WARRANT = FIX / "commodity_shfe_warrant.json"

COPPER_INSTS = [
    {"id": "cu-shfe-price", "code": "CU0", "metric": "price"},
    {"id": "cu-spot-price", "code": "CU", "metric": "price"},
    {"id": "cu-warrant", "code": "CU-WARRANT", "metric": "warehouse"},
]
GOLD_INSTS = [
    {"id": "au-shfe-price", "code": "AU0", "metric": "price"},
    {"id": "au-warrant", "code": "AU-WARRANT", "metric": "warehouse"},
]
ALUMINA_INSTS = [{"id": "ao-warrant", "code": "AO-WARRANT", "metric": "warehouse"}]
ALL_WARRANT_INSTS = [
    {"id": "cu-warrant", "code": "CU-WARRANT", "metric": "warehouse"},
    {"id": "al-warrant", "code": "AL-WARRANT", "metric": "warehouse"},
    {"id": "ao-warrant", "code": "AO-WARRANT", "metric": "warehouse"},
    {"id": "au-warrant", "code": "AU-WARRANT", "metric": "warehouse"},
]


def warrant_fixture() -> dict[str, pd.DataFrame]:
    return {k: pd.DataFrame(v)
            for k, v in json.loads(FIX_WARRANT.read_text(encoding="utf-8")).items()}


def detail_sum(frames: dict[str, pd.DataFrame], key: str) -> float:
    """fixture 明细行（ROWSTATUS=0）仓单量求和——与实现同口径的期望值基准。"""
    df = frames[key]
    rs = pd.to_numeric(df["ROWSTATUS"], errors="coerce")
    return float(pd.to_numeric(df.loc[rs == 0, "WRTWGHTS"], errors="coerce").dropna().sum())


def build_fetcher_stub(monkeypatch, instruments, *, daily=..., spot=..., receipt=...):
    """三接口全部打桩并带调用探针；daily/spot/receipt 缺省(sentinel)回放 fixture，
       传 DataFrame/dict 直接回放，传 callable 则透传调用（可表达抛异常）。"""
    import backend.fetchers.akshare_composite as mod
    calls = {"daily": [], "spot": [], "receipt": []}

    def fake_daily(symbol):
        calls["daily"].append(symbol)
        if callable(daily):
            return daily(symbol)
        return pd.read_json(FIX_DAILY) if daily is ... else daily

    def fake_spot(date, vars_list):
        calls["spot"].append((date, tuple(vars_list)))
        if callable(spot):
            return spot(date, vars_list)
        return pd.read_json(FIX_SPOT) if spot is ... else spot

    def fake_receipt(date):
        calls["receipt"].append(date)
        if callable(receipt):
            return receipt(date)
        return warrant_fixture() if receipt is ... else receipt

    monkeypatch.setattr(mod, "ak", types.SimpleNamespace(
        futures_zh_daily_sina=fake_daily,
        futures_spot_price=fake_spot,
        futures_shfe_warehouse_receipt=fake_receipt))
    src = {"id": "copper", "name": "铜", "fetcher": "akshare.composite"}
    return build_fetcher("akshare.composite", src, instruments), calls


# ---------- SHFE 日K（全历史型） ----------

def test_daily_full_history_ignores_trade_date(monkeypatch):
    # 全历史型：fetch 忽略入参 trade_date，整段 fixture（500 行）逐日返回
    f, calls = build_fetcher_stub(monkeypatch, GOLD_INSTS[:1])
    obs = f.fetch("2026-10-16")
    n_fix = len(pd.read_json(FIX_DAILY))
    assert len(obs) == n_fix == 500
    assert calls["daily"] == ["AU0"] and calls["spot"] == [] and calls["receipt"] == []
    obs2 = f.fetch("2000-01-01")            # 传历史日期结果一致——参数被忽略
    assert len(obs2) == n_fix


def test_daily_rows_normalized_and_mapped(monkeypatch):
    f, _ = build_fetcher_stub(monkeypatch, GOLD_INSTS[:1])
    obs = f.fetch("2026-10-16")
    assert all(o.instrument_id == "au-shfe-price" for o in obs)
    assert all(o.metric == "price" for o in obs)
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", o.trade_date) for o in obs)
    assert max(o.trade_date for o in obs) == "2026-10-16"   # fixture 最后一行
    last = pd.read_json(FIX_DAILY).iloc[-1]
    assert obs[-1].value == pytest.approx(float(last["close"]))


def test_daily_nan_close_row_skipped(monkeypatch):
    df = pd.read_json(FIX_DAILY)
    df.loc[df.index[-1], "close"] = float("nan")            # 尾行 close=NaN → 跳行
    f, _ = build_fetcher_stub(monkeypatch, GOLD_INSTS[:1], daily=df)
    obs = f.fetch("2026-10-16")
    assert len(obs) == len(df) - 1
    assert all(o.trade_date != "2026-10-16" for o in obs)


def test_daily_empty_df_returns_empty(monkeypatch):
    f, _ = build_fetcher_stub(monkeypatch, GOLD_INSTS[:1], daily=pd.DataFrame())
    assert f.fetch("2026-10-16") == []


def test_daily_missing_close_column_raises(monkeypatch):
    # fail-loud：缺收盘价列须抛 ValueError，而非静默产出错误数据
    f, _ = build_fetcher_stub(
        monkeypatch, GOLD_INSTS[:1],
        daily=pd.DataFrame([{"date": "2026-10-15", "open": 886.48}]))
    with pytest.raises(ValueError):
        f.fetch("2026-10-16")


# ---------- 生意社现货（单日型） ----------

def test_spot_values_and_date_normalized(monkeypatch):
    # 铜铝两个现货成分（铝属 aluminum 源，此处并置覆盖两品种映射）
    insts = [{"id": "cu-spot-price", "code": "CU", "metric": "price"},
             {"id": "al-spot-price", "code": "AL", "metric": "price"}]
    f, calls = build_fetcher_stub(monkeypatch, insts)
    obs = f.fetch("2026-10-15")
    by_id = {o.instrument_id: o for o in obs}
    fix = pd.read_json(FIX_SPOT)
    cu = fix[fix["symbol"] == "CU"].iloc[0]
    al = fix[fix["symbol"] == "AL"].iloc[0]
    assert by_id["cu-spot-price"].value == pytest.approx(float(cu["spot_price"]))
    assert by_id["al-spot-price"].value == pytest.approx(float(al["spot_price"]))
    # fixture 的 date 为 '20261015' 字符串（read_json 自动转 Timestamp）→ 归一 YYYY-MM-DD
    assert by_id["cu-spot-price"].trade_date == "2026-10-15"
    assert by_id["cu-spot-price"].metric == "price"
    assert calls["spot"] == [("20261015", ("AL", "CU"))]     # 一次调用带全部品种（sorted）
    # 100ppi 已下架氧化铝：fixture 无 AO 行 → 无 ao-spot-price 观测（如实缺数据）
    assert "ao-spot-price" not in by_id


def test_spot_maps_ao_when_present(monkeypatch):
    # AO 映射路径覆盖：100ppi 恢复氧化铝后无需改代码自动接入
    df = pd.DataFrame([
        {"date": "20261015", "symbol": "AO", "spot_price": 3500.0},
    ])
    f, calls = build_fetcher_stub(
        monkeypatch, [{"id": "ao-spot-price", "code": "AO", "metric": "price"}],
        spot=df)
    obs = f.fetch("2026-10-15")
    assert len(obs) == 1 and obs[0].instrument_id == "ao-spot-price"
    assert obs[0].value == pytest.approx(3500.0)
    assert obs[0].trade_date == "2026-10-15"
    assert calls["spot"] == [("20261015", ("AO",))]


def test_spot_unregistered_symbol_ignored(monkeypatch):
    # 接口偶发多回/未注册品种：不产观测
    df = pd.DataFrame([
        {"date": "20261015", "symbol": "CU", "spot_price": 110535.0},
        {"date": "20261015", "symbol": "ZN", "spot_price": 1.0},
    ])
    f, _ = build_fetcher_stub(monkeypatch, COPPER_INSTS[1:2], spot=df)
    obs = f.fetch("2026-10-15")
    assert [o.instrument_id for o in obs] == ["cu-spot-price"]


def test_spot_nan_price_row_skipped(monkeypatch):
    df = pd.DataFrame([
        {"date": "20261015", "symbol": "CU", "spot_price": float("nan")},
        {"date": "20261015", "symbol": "AL", "spot_price": 23283.33},
    ])
    f, _ = build_fetcher_stub(
        monkeypatch, [{"id": "cu-spot-price", "code": "CU", "metric": "price"},
                      {"id": "al-spot-price", "code": "AL", "metric": "price"}],
        spot=df)
    obs = f.fetch("2026-10-15")
    assert [o.instrument_id for o in obs] == ["al-spot-price"]


def test_spot_missing_price_column_raises(monkeypatch):
    f, _ = build_fetcher_stub(
        monkeypatch, COPPER_INSTS[1:2],
        spot=pd.DataFrame([{"date": "20261015", "symbol": "CU"}]))
    with pytest.raises(ValueError):
        f.fetch("2026-10-15")


def test_spot_empty_df_returns_empty(monkeypatch):
    f, _ = build_fetcher_stub(monkeypatch, COPPER_INSTS[1:2], spot=pd.DataFrame())
    assert f.fetch("2026-10-15") == []


# ---------- 上期所仓单（单日型，dict 按品种明细行求和） ----------

def test_warrant_values_from_fixture(monkeypatch):
    # 期望值先按 fixture 明细求和算出（与实现同口径），再对四品种锁值：
    # 铜=34933 吨、铝=95072 吨、氧化铝=仓库+厂库=225560+12900=238460 吨、黄金=116028 千克
    f, calls = build_fetcher_stub(monkeypatch, ALL_WARRANT_INSTS)
    obs = f.fetch("2026-10-15")
    frames = warrant_fixture()
    by_id = {o.instrument_id: o for o in obs}
    assert calls["receipt"] == ["20261015"]                  # 整 dict 只拉一次
    assert by_id["cu-warrant"].value == pytest.approx(detail_sum(frames, "铜"))
    assert by_id["al-warrant"].value == pytest.approx(detail_sum(frames, "铝"))
    ao = detail_sum(frames, "氧化铝仓库") + detail_sum(frames, "氧化铝厂库")
    assert by_id["ao-warrant"].value == pytest.approx(ao)
    assert by_id["au-warrant"].value == pytest.approx(detail_sum(frames, "黄金"))
    # fixture 内容锁（2026-10-15 实测）——重录 fixture 后如数值漂移须人工确认口径
    assert by_id["cu-warrant"].value == pytest.approx(34933.0)
    assert by_id["al-warrant"].value == pytest.approx(95072.0)
    assert by_id["ao-warrant"].value == pytest.approx(238460.0)
    assert by_id["au-warrant"].value == pytest.approx(116028.0)
    assert all(o.metric == "warehouse" and o.trade_date == "2026-10-15" for o in obs)


def test_warrant_detail_sum_matches_exchange_total(monkeypatch):
    # 聚合口径与交易所"总计"行核对（fixture 内建一致性）；黄金无总计行（单条明细即全量）
    frames = warrant_fixture()
    assert detail_sum(frames, "铜") == 34933
    assert detail_sum(frames, "铝") == 95072
    assert detail_sum(frames, "氧化铝仓库") == 225560
    assert detail_sum(frames, "氧化铝厂库") == 12900
    assert detail_sum(frames, "白银") == 1415307
    for key in ["铜", "铝", "氧化铝仓库", "氧化铝厂库", "白银"]:
        df = frames[key]
        total = df[df["WHABBRNAME"] == "总计"]["WRTWGHTS"]
        assert len(total) == 1 and detail_sum(frames, key) == float(total.iloc[0])
    gold = frames["黄金"]
    assert (gold["ROWSTATUS"].astype(str) == "2").sum() == 0  # 黄金无总计行
    assert len(gold) == 1


def test_warrant_rowstatus_numeric_tolerant(monkeypatch):
    # 未来 akshare 若改回整数 ROWSTATUS，汇总逻辑不漂移
    frames = warrant_fixture()
    cu = frames["铜"].copy()
    cu["ROWSTATUS"] = pd.to_numeric(cu["ROWSTATUS"])
    f, _ = build_fetcher_stub(monkeypatch, ALL_WARRANT_INSTS[:1], receipt={"铜": cu})
    obs = f.fetch("2026-10-15")
    assert obs[0].value == pytest.approx(detail_sum(frames, "铜"))


def test_warrant_missing_subkey_skips_frame(monkeypatch):
    # 氧化铝厂库缺失 → 只按仓库帧汇总（部分缺帧不炸、不补 0）
    frames = warrant_fixture()
    f, _ = build_fetcher_stub(
        monkeypatch, ALUMINA_INSTS,
        receipt={"氧化铝仓库": frames["氧化铝仓库"]})
    obs = f.fetch("2026-10-15")
    assert obs[0].value == pytest.approx(detail_sum(frames, "氧化铝仓库"))


def test_warrant_all_keys_missing_no_observation(monkeypatch):
    # 品种全帧缺失 → 该成分无观测（绝不编造 0 仓单）
    f, _ = build_fetcher_stub(
        monkeypatch, ALL_WARRANT_INSTS,
        receipt={"锌": warrant_fixture()["铝"]})   # 只有无关品种
    assert f.fetch("2026-10-15") == []


def test_warrant_nan_weight_row_skipped(monkeypatch):
    df = pd.DataFrame([
        {"VARNAME": "铜", "REGNAME": "上海", "WHABBRNAME": "中储吴淞",
         "WRTWGHTS": 1000, "WRTCHANGE": 0, "ROWSTATUS": "0"},
        {"VARNAME": "铜", "REGNAME": "上海", "WHABBRNAME": "坏行",
         "WRTWGHTS": float("nan"), "WRTCHANGE": 0, "ROWSTATUS": "0"},
        {"VARNAME": "铜", "REGNAME": "上海", "WHABBRNAME": "中储南京",
         "WRTWGHTS": 500, "WRTCHANGE": 0, "ROWSTATUS": "0"},
    ])
    f, _ = build_fetcher_stub(monkeypatch, ALL_WARRANT_INSTS[:1], receipt={"铜": df})
    obs = f.fetch("2026-10-15")
    assert len(obs) == 1 and obs[0].value == pytest.approx(1500.0)


def test_warrant_missing_weight_column_raises(monkeypatch):
    frames = warrant_fixture()
    bad = frames["铜"].drop(columns=["WRTWGHTS"])
    f, _ = build_fetcher_stub(monkeypatch, ALL_WARRANT_INSTS[:1], receipt={"铜": bad})
    with pytest.raises(ValueError):
        f.fetch("2026-10-15")


def test_warrant_empty_dict_returns_empty(monkeypatch):
    f, _ = build_fetcher_stub(monkeypatch, ALL_WARRANT_INSTS, receipt={})
    assert f.fetch("2026-10-15") == []


# ---------- 复合分流（短路/合并/原子） ----------

def test_merged_source_all_branches_called_once(monkeypatch):
    # 铜源三成分：日K+现货+仓单合并返回，三接口各恰调一次
    f, calls = build_fetcher_stub(monkeypatch, COPPER_INSTS)
    obs = f.fetch("2026-10-15")
    assert {o.instrument_id for o in obs} == {
        "cu-shfe-price", "cu-spot-price", "cu-warrant"}
    assert len(calls["daily"]) == 1 and calls["daily"] == ["CU0"]
    assert len(calls["spot"]) == 1 and len(calls["receipt"]) == 1


def test_short_circuit_daily_only_source(monkeypatch):
    # 只含 CU0 的源：不得触碰现货/仓单接口
    f, calls = build_fetcher_stub(
        monkeypatch, [{"id": "cu-shfe-price", "code": "CU0", "metric": "price"}])
    obs = f.fetch("2026-10-16")
    assert all(o.instrument_id == "cu-shfe-price" for o in obs)
    assert calls["daily"] == ["CU0"]
    assert calls["spot"] == [] and calls["receipt"] == []


def test_short_circuit_spot_only_source(monkeypatch):
    f, calls = build_fetcher_stub(
        monkeypatch, [{"id": "cu-spot-price", "code": "CU", "metric": "price"}])
    f.fetch("2026-10-15")
    assert calls["spot"] == [("20261015", ("CU",))]
    assert calls["daily"] == [] and calls["receipt"] == []


def test_short_circuit_warrant_only_source(monkeypatch):
    # 仓单源共用一次整 dict 拉取；不触日K/现货接口
    f, calls = build_fetcher_stub(monkeypatch, ALL_WARRANT_INSTS)
    obs = f.fetch("2026-10-15")
    assert {o.instrument_id for o in obs} == {
        "cu-warrant", "al-warrant", "ao-warrant", "au-warrant"}
    assert calls["receipt"] == ["20261015"]
    assert calls["daily"] == [] and calls["spot"] == []


def test_branch_failure_isolated_partial_success(monkeypatch, caplog):
    # v1.2.1 契约变更（原"任一分支异常整体抛"）：仓单分支挂 → 告警跳过，
    # 日K/现货分支照常产出（部分可用数据不再被单接口故障拖死）
    def boom(date):
        raise RuntimeError("shfe warrant down")

    f, _ = build_fetcher_stub(monkeypatch, COPPER_INSTS, receipt=boom)
    obs = f.fetch("2026-10-15")
    ids = {o.instrument_id for o in obs}
    assert {"cu-shfe-price", "cu-spot-price"} <= ids
    assert "cu-warrant" not in ids
    assert "shfe_warrant" in caplog.text and "shfe warrant down" in caplog.text


def test_all_interfaces_empty_returns_empty(monkeypatch):
    f, _ = build_fetcher_stub(
        monkeypatch, COPPER_INSTS,
        daily=pd.DataFrame(), spot=pd.DataFrame(), receipt={})
    assert f.fetch("2026-10-15") == []


def test_lag_days_is_one(monkeypatch):
    f, _ = build_fetcher_stub(monkeypatch, COPPER_INSTS)
    assert f.lag_days == 1


# ---------- _util.normalize_date 契约扩展（'YYYYMMDD' 字符串形态） ----------

@pytest.mark.parametrize("v,expected", [
    ("20261015", "2026-10-15"),               # 8 位数字字符串（生意社现货实测形态）
    ("2026/10/15", "2026-10-15"),             # 斜杠（既有形态，防回归）
    ("2026-10-15", "2026-10-15"),             # 标准形态
])
def test_normalize_date_string_forms(v, expected):
    assert normalize_date(v) == expected


def test_normalize_date_yyyymmdd_int_still_works():
    assert normalize_date(20261015) == "2026-10-15"   # 既有整数形态不受影响
