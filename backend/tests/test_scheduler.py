import datetime as dt
import pytest
from backend import scheduler as S
from backend.fetchers.base import Observation

@pytest.fixture(autouse=True)
def _no_calendar(monkeypatch):
    """调度器测试不触网：交易日历走工作日降级（可用例内覆盖）"""
    monkeypatch.setattr(S, "_trade_days", lambda: None)

def test_next_run_same_day_before_1640():
    now = dt.datetime(2026, 10, 16, 10, 0)   # 周五 10:00
    assert S.next_run(now) == dt.datetime(2026, 10, 16, 16, 40)

def test_next_run_after_1640_goes_next_workday():
    now = dt.datetime(2026, 10, 16, 17, 0)   # 周五 17:00 → 下周一 16:40
    assert S.next_run(now) == dt.datetime(2026, 10, 19, 16, 40)

def test_next_run_skips_weekend():
    now = dt.datetime(2026, 10, 17, 10, 0)   # 周六 → 周一 16:40
    assert S.next_run(now) == dt.datetime(2026, 10, 19, 16, 40)

def test_next_run_uses_calendar(monkeypatch):
    # 日历可用：10-19(周一)~10-21 是交易日，10-16 之后…构造下一个触发日跨节假日
    days = ("2026-10-16", "2026-10-21", "2026-10-22")   # 10-17~10-20 休市
    monkeypatch.setattr(S, "_trade_days", lambda: days)
    now = dt.datetime(2026, 10, 16, 17, 0)   # 周五 17:00 → 下个交易日 10-21 的 16:40
    assert S.next_run(now) == dt.datetime(2026, 10, 21, 16, 40)

def test_next_run_calendar_exhausted_falls_back_to_weekday(monkeypatch):
    # 日历只覆盖到 2026 年末（新浪日历实际覆盖到当年年末）：常驻进程不停机跨年时
    # next_run 对 2027 日期不得恒 False 死循环假死——超出日历上界按工作日降级
    days = ("2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31")
    monkeypatch.setattr(S, "_trade_days", lambda: days)
    now = dt.datetime(2026, 12, 31, 17, 0)   # 周四 17:00：日历当天到期
    assert S.next_run(now) == dt.datetime(2027, 1, 1, 16, 40)   # 超界降级：周五按工作日放行
    now = dt.datetime(2027, 1, 2, 10, 0)     # 周六，已深度超出日历上界
    assert S.next_run(now) == dt.datetime(2027, 1, 4, 16, 40)   # 降级跳周末 → 周一 16:40

def test_next_run_loop_cap_failsafe(monkeypatch):
    # 二道保险：即便 _is_trade_day 持续 False（日历 pathology），400 次迭代上限后
    # 也按工作日降级返回，常驻进程绝不假死
    monkeypatch.setattr(S, "_is_trade_day", lambda d: False)
    nxt = S.next_run(dt.datetime(2026, 10, 16, 17, 0))   # 周五 17:00
    assert nxt == dt.datetime(2027, 11, 22, 16, 40)   # +400 天后的下一个工作日（周一）

def test_missed_dates_returns_uncovered_trading_days(monkeypatch, conn):
    # 库内 hs300-etf 最新 2026-10-13；今天 10-16 → 错过 10-14、10-15（10-16 是今天，16:40 未到不算）
    from backend import collect as C
    monkeypatch.setattr(C, "_trade_days", lambda: None)
    monkeypatch.setattr(S, "_trade_days", lambda: None)
    # 离线收口：run_source 不触网，固定 Observation 只为推进 source_max_date
    monkeypatch.setattr(C, "fetch_with_retry",
                        lambda f, d: [Observation("510300", "share", d, 42.0)])
    src = conn.execute("SELECT * FROM sources WHERE id='hs300-etf'").fetchone()
    insts = conn.execute("SELECT * FROM instruments WHERE source_id='hs300-etf'").fetchall()
    C.run_source(conn, src, insts, "2026-10-14")   # target=10-13
    days = S.missed_dates(conn, dt.date(2026, 10, 16))
    assert "2026-10-14" in days and "2026-10-15" in days
    assert "2026-10-16" not in days   # 今天的采集目标(10-15)由当日 16:40 例行跑覆盖

def test_catchup_runs_backfill_for_stale_sources(monkeypatch, conn):
    monkeypatch.setattr(S, "_trade_days", lambda: None)
    from backend import collect as C
    monkeypatch.setattr(C, "_trade_days", lambda: None)
    # 离线收口：run_source 不触网，固定 Observation 只为推进 source_max_date
    monkeypatch.setattr(C, "fetch_with_retry",
                        lambda f, d: [Observation("510300", "share", d, 42.0)])
    called = {}
    monkeypatch.setattr(S, "_run_catchup_backfill",
                        lambda conn, source, d_from: called.setdefault("d_from", d_from) or 0)
    src = conn.execute("SELECT * FROM sources WHERE id='hs300-etf'").fetchone()
    insts = conn.execute("SELECT * FROM instruments WHERE source_id='hs300-etf'").fetchall()
    C.run_source(conn, src, insts, "2026-10-14")   # 库内最新 target=10-13
    n = S.catchup_if_stale(conn, today=dt.date(2026, 10, 16))
    assert n == 1 and called["d_from"] == "2026-10-14"   # 从 max+1 起补
