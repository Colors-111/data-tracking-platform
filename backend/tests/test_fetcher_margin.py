"""融资融券 fetcher 测试（v1.4 设计 §5.4）：三接口编排、元→亿元换算、
   万人换算、早期 NaN 跳行、分支隔离、交叉校验三态（含远古节流）、fail-loud。"""
import datetime as dt
import logging
import math
from pathlib import Path

import pandas as pd
import pytest

from backend.fetchers.base import build_fetcher

FIX = Path(__file__).parent / "fixtures"

# 取 fixture 倒数第二行（2026-10-20），保证有完整交易日；校验节流测试用它注入确定"今天"
FIX_LATEST = "2026-10-20"

# 账户统计三仪器 id（两测试共用；与乐咕 margin-{sh,sz}-* 命名空间区分）
ACCT_IDS = ("margin-maintenance", "margin-collateral", "margin-investors")


def _raise(msg):
    return lambda *a, **k: (_ for _ in ()).throw(RuntimeError(msg))


@pytest.fixture
def margin_env(conn, monkeypatch):
    src = conn.execute("SELECT * FROM sources WHERE id='margin'").fetchone()
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id='margin' ORDER BY sort_order"
    ).fetchall()
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sh",
                        lambda: pd.read_json(FIX / "margin_lg_sh.json"))
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sz",
                        lambda: pd.read_json(FIX / "margin_lg_sz.json"))
    monkeypatch.setattr(mod.ak, "stock_margin_account_info",
                        lambda: pd.read_json(FIX / "margin_account.json"))
    # 校验通道默认断连（走静默路径）——校验专项测试单独覆盖
    monkeypatch.setattr(mod.ak, "stock_margin_sse", _raise("SSE net down"))
    monkeypatch.setattr(mod.ak, "stock_margin_szse", _raise("SZSE net down"))
    monkeypatch.setattr(mod, "_today", lambda: dt.date.fromisoformat(FIX_LATEST))
    return build_fetcher(src["fetcher"], src, insts)


def test_unknown_code_fails_loud(conn):
    # 构建期未知 code raise（seed 拼错当场暴露，禁静默丢数据）
    src = conn.execute("SELECT * FROM sources WHERE id='margin'").fetchone()
    insts = [{"id": "x", "code": "MARGIN-XX-FOO", "metric": "financing"}]
    from backend.fetchers.akshare_margin import MarginFetcher
    with pytest.raises(ValueError, match="未支持的仪器代码"):
        MarginFetcher(src, insts)


def test_lg_produces_yi(margin_env):
    # 乐咕元 → ÷1e8 亿元：fixture 尾行沪融资余额 ≈1.26e12 元 ≈ 12580 亿
    obs = margin_env.fetch(FIX_LATEST)
    by = {(o.instrument_id, o.trade_date): o.value for o in obs}
    sh_fin = [v for (iid, d), v in by.items()
              if iid == "margin-sh-financing" and d == FIX_LATEST]
    assert sh_fin and 12000 < sh_fin[0] < 13500        # 亿元量级（2026-10 实测）
    assert ("margin-sz-financing", FIX_LATEST) in by   # 深市同日也在

    def val(iid):
        return next(v for (i, d), v in by.items() if i == iid and d == FIX_LATEST)

    # 两融余额 == 融资余额 + 融券余额（fixture 精确成立，防列映射错位）
    assert abs(val("margin-sh-total") - val("margin-sh-financing")
               - val("margin-sh-securities")) < 1e-6


def test_full_history_ignores_trade_date(margin_env):
    # 全历史型契约：fetch 忽略 trade_date 全量返回（backfill 首日全量入库的门）
    obs = margin_env.fetch("2020-01-02")
    dates = {o.trade_date for o in obs}
    assert len(dates) > 100 and "2020-01-02" in dates


def test_branch_isolation(margin_env, monkeypatch, caplog):
    # 乐咕 SH 挂 → SZ + 账户统计照常产出（run_branches 契约）
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sh", _raise("SH down"))
    obs = margin_env.fetch(FIX_LATEST)
    iids = {o.instrument_id for o in obs}
    assert "margin-sz-financing" in iids and "margin-sh-financing" not in iids
    assert "margin-maintenance" in iids          # 账户分支不受 SH 断连影响
    assert "lg_sh" in caplog.text


def test_missing_value_column_raises(margin_env, monkeypatch):
    # 值列改名 → ValueError（run_branches 不隔离，整源 failed 可见）
    import backend.fetchers.akshare_margin as mod
    df = pd.read_json(FIX / "margin_lg_sh.json").drop(columns=["融券余额"])
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sh", lambda: df)
    with pytest.raises(ValueError, match="margin-sh-securities"):
        margin_env.fetch(FIX_LATEST)


def test_empty_upstream_returns_empty(margin_env, monkeypatch):
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sh", lambda: pd.DataFrame())
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sz", lambda: pd.DataFrame())
    monkeypatch.setattr(mod.ak, "stock_margin_account_info", lambda: pd.DataFrame())
    # 三分支各自成功后 obs 空——run_branches 不抛（失败≠空），返回 []
    assert margin_env.fetch(FIX_LATEST) == []


def test_only_configured_branches_count(conn, monkeypatch):
    # 仅账户分支配置 + 账户挂 → RuntimeError（空分支不占分母）
    src = conn.execute("SELECT * FROM sources WHERE id='margin'").fetchone()
    insts = [{"id": "margin-maintenance", "code": "MARGIN-MAINTENANCE"}]
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "stock_margin_account_info", _raise("down"))
    F = build_fetcher(src["fetcher"], src, insts)
    with pytest.raises(RuntimeError, match="全部分支失败"):
        F.fetch(FIX_LATEST)


def test_duplicate_code_fails_loud(conn):
    # 同一后缀重复注册 → 构建期 ValueError（防 seed 拼重复静默覆盖）
    src = conn.execute("SELECT * FROM sources WHERE id='margin'").fetchone()
    insts = [
        {"id": "margin-maintenance", "code": "MARGIN-MAINTENANCE"},
        {"id": "margin-maintenance-dup", "code": "MARGIN-MAINTENANCE"},
    ]
    from backend.fetchers.akshare_margin import MarginFetcher
    with pytest.raises(ValueError, match="重复"):
        MarginFetcher(src, insts)


def test_all_branches_fail_reraises(margin_env, monkeypatch):
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sh", _raise("a"))
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sz", _raise("b"))
    monkeypatch.setattr(mod.ak, "stock_margin_account_info", _raise("c"))
    with pytest.raises(RuntimeError, match="全部分支失败"):
        margin_env.fetch(FIX_LATEST)


def test_account_units(margin_env):
    # 账户统计：担保比例 %/担保物 亿元原样、投资者数 个 → ÷1e4 万人
    # 区间按数量级设定（非行情预测）——fixture 重录后数值漂移不脆断，
    # 但换算因子写错（÷1e3/÷1e5/÷1e8/×100）必越界
    obs = margin_env.fetch(FIX_LATEST)
    by = {(o.instrument_id, o.trade_date): o.value for o in obs}
    assert 100 < by[("margin-maintenance", FIX_LATEST)] < 500        # % 量级
    assert 5 < by[("margin-investors", FIX_LATEST)] < 100            # 万人（fixture 历史 7.35–76.16）
    assert 1e4 < by[("margin-collateral", FIX_LATEST)] < 2e5         # 亿元
    # 锁 (iid, metric) 对应关系——collateral/investors 写反也能抓到
    pairs = {(o.instrument_id, o.metric) for o in obs
             if o.instrument_id in ACCT_IDS}
    assert pairs == {("margin-maintenance", "maintenance"),
                     ("margin-collateral", "collateral"),
                     ("margin-investors", "investors")}


def test_account_early_nan_skipped(margin_env):
    # 2012-09-27 首行：担保物/投资者数 NaN → 只产出 maintenance，不产 nan/崩
    obs = margin_env.fetch(FIX_LATEST)
    first = [o for o in obs
             if o.trade_date == "2012-09-27" and o.instrument_id in ACCT_IDS]
    assert {o.instrument_id for o in first} == {"margin-maintenance"}
    account_obs = [o for o in obs if o.instrument_id in ACCT_IDS]
    assert all(math.isfinite(o.value) for o in account_obs)   # 无 NaN/inf 混入


# ---------- 交叉校验通道（Task 5） ----------
# SSE 直连真实样本（2026-10-21 实测列与值；信用交易日期为 YYYYMMDD str）
def _sse_df(date_str, financing, total, securities=1.9e10, buying=None):
    d8 = date_str.replace("-", "")
    if buying is None:
        buying = financing * 0.05
    return pd.DataFrame([{"信用交易日期": d8, "融资余额": financing,
                          "融资买入额": buying, "融券余量": 3.2e9,
                          "融券余量金额": securities, "融券卖出量": 6.9e7,
                          "融资融券余额": total}])


def test_cross_check_warns_on_mismatch(margin_env, monkeypatch, caplog):
    # SSE 直连值偏离乐咕 2%（>0.5% 容差）→ warning 告警
    # 取 FIX_LATEST 当日行（iloc[-1] 是次日 2026-10-21，日期列对不上不参与比对）
    import backend.fetchers.akshare_margin as mod
    src = pd.read_json(FIX / "margin_lg_sh.json").iloc[-2]
    monkeypatch.setattr(
        mod.ak, "stock_margin_sse",
        lambda start_date, end_date: _sse_df(
            FIX_LATEST, src["融资余额"] * 1.02, src["融资融券余额"]))
    margin_env.fetch(FIX_LATEST)
    assert "不一致" in caplog.text and "financing" in caplog.text


def test_cross_check_within_tolerance(margin_env, monkeypatch, caplog):
    # 差 0.1%（容差内）→ 无 warning（四项指标全部贴合乐咕）
    import backend.fetchers.akshare_margin as mod
    src = pd.read_json(FIX / "margin_lg_sh.json").iloc[-2]

    def f(col):
        return src[col] * 1.001

    monkeypatch.setattr(
        mod.ak, "stock_margin_sse",
        lambda start_date, end_date: _sse_df(
            FIX_LATEST, f("融资余额"), f("融资融券余额"),
            securities=f("融券余额"), buying=f("融资买入额")))
    margin_env.fetch(FIX_LATEST)
    assert "不一致" not in caplog.text


def test_cross_check_silent_on_direct_failure(margin_env, monkeypatch, caplog):
    # 直连断连（env 默认 _raise）→ info 静默，产出不受影响、无 warning
    caplog.set_level(logging.INFO)
    obs = margin_env.fetch(FIX_LATEST)
    assert obs and "不一致" not in caplog.text and "SSE net down" in caplog.text


def test_cross_check_skipped_for_old_dates(margin_env, monkeypatch):
    # 远古目标日（>30 日历日）→ 零直连调用（backfill 2010 起不逐日校验）
    import backend.fetchers.akshare_margin as mod
    calls = []

    def probe(start_date, end_date):
        calls.append(start_date)
        return _sse_df(FIX_LATEST, 1.0, 1.0)

    monkeypatch.setattr(mod.ak, "stock_margin_sse", probe)
    margin_env.fetch("2020-01-02")
    assert calls == []


def test_cross_check_structure_change_warns(margin_env, monkeypatch, caplog):
    # SSE 列结构变更（缺日期列 col() ValueError）→ warning 而非静默
    # （校验通道结构变更必须可见，否则永久失去校验能力无人知）
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "stock_margin_sse",
                        lambda start_date, end_date: pd.DataFrame([{"x": 1}]))
    margin_env.fetch(FIX_LATEST)
    assert "结构变更" in caplog.text
