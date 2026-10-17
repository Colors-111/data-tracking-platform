"""v1.3 全市场快照采集与 local.snapshot fetcher 单测（不触网：
   akshare 层以 fake df 直接替换 _fetch_*_snapshot，绕过 tenacity 等待）。"""
import pandas as pd
import pytest

from backend import collect as C
from backend.app import db
from backend.fetchers.base import build_fetcher
from backend.fetchers.local_snapshot import LocalSnapshotFetcher

SSE_DF = pd.DataFrame([
    {"基金代码": "510300", "基金简称": "沪深300ETF", "基金份额": 24119590000},
    {"基金代码": 510050, "基金简称": "上证50ETF", "基金份额": 7300000000},
])
SZSE_DF = pd.DataFrame([
    {"基金代码": "159915", "基金简称": "创业板ETF", "基金份额": 5000000000},
])


@pytest.fixture(autouse=True)
def _no_calendar(monkeypatch):
    """单元测试不触网：交易日历强制走工作日降级（与 test_collect 同口径）。"""
    monkeypatch.setattr(C, "_trade_days", lambda: None)


# ---------- collect_market_snapshot ----------

def test_snapshot_writes_both_exchanges(conn, monkeypatch):
    monkeypatch.setattr(C, "_fetch_sse_snapshot", lambda d8: SSE_DF)
    monkeypatch.setattr(C, "_fetch_szse_snapshot", lambda d8: SZSE_DF)
    assert C.collect_market_snapshot(conn, "2026-10-15", "t1") == 3
    rows = conn.execute(
        "SELECT * FROM etf_snapshots ORDER BY code").fetchall()
    assert [(r["code"], r["exchange"]) for r in rows] == [
        ("159915", "SZSE"), ("510050", "SSE"), ("510300", "SSE")]
    by = {r["code"]: r for r in rows}
    assert by["510300"]["shares"] == pytest.approx(241.1959)   # 份 → 亿份
    assert by["510300"]["name"] == "沪深300ETF"
    assert by["510300"]["trade_date"] == "2026-10-15"
    assert by["510300"]["fetched_at"] == "t1"
    assert by["510050"]["code"] == "510050"   # 整型代码归一 6 位


def test_snapshot_idempotent(conn, monkeypatch):
    monkeypatch.setattr(C, "_fetch_sse_snapshot", lambda d8: SSE_DF)
    monkeypatch.setattr(C, "_fetch_szse_snapshot", lambda d8: SZSE_DF)
    assert C.collect_market_snapshot(conn, "2026-10-15", "t1") == 3
    assert C.collect_market_snapshot(conn, "2026-10-15", "t2") == 3
    assert conn.execute(
        "SELECT COUNT(*) c FROM etf_snapshots").fetchone()["c"] == 3
    assert conn.execute(
        "SELECT fetched_at f FROM etf_snapshots WHERE code='510300'"
    ).fetchone()["f"] == "t2"   # INSERT OR REPLACE 幂等覆盖


def test_szse_failure_isolated_sse_still_written(conn, monkeypatch, caplog):
    # 深交所近期间歇性 connection refused：单所失败只告警跳过，不抛、不写 job_runs
    monkeypatch.setattr(C, "_fetch_sse_snapshot", lambda d8: SSE_DF)

    def boom(d8):
        raise RuntimeError("szse refused")

    monkeypatch.setattr(C, "_fetch_szse_snapshot", boom)
    assert C.collect_market_snapshot(conn, "2026-10-15", "t1") == 2   # 不抛
    assert {r["exchange"] for r in conn.execute(
        "SELECT exchange FROM etf_snapshots")} == {"SSE"}
    assert "SZSE" in caplog.text and "szse refused" in caplog.text


def test_both_exchanges_fail_returns_zero(conn, monkeypatch):
    def boom(d8):
        raise RuntimeError("down")

    monkeypatch.setattr(C, "_fetch_sse_snapshot", boom)
    monkeypatch.setattr(C, "_fetch_szse_snapshot", boom)
    assert C.collect_market_snapshot(conn, "2026-10-15", "t1") == 0   # 不抛
    assert conn.execute(
        "SELECT COUNT(*) c FROM etf_snapshots").fetchone()["c"] == 0


def test_snapshot_empty_df(conn, monkeypatch):
    monkeypatch.setattr(C, "_fetch_sse_snapshot", lambda d8: pd.DataFrame())
    monkeypatch.setattr(C, "_fetch_szse_snapshot", lambda d8: None)
    assert C.collect_market_snapshot(conn, "2026-10-15", "t1") == 0


# ---------- main() 接线：调用点与门控 ----------

def test_main_daily_snapshot_runs_before_sources(conn, monkeypatch):
    """快照必须先于逐源循环写入：自选源 local.snapshot 从快照表读本次目标日
       （前一交易日）。若放循环后，循环读到的永远是上一次运行写入的更早一天，
       etf-watchlist 将恒为 empty（时间线推演见设计 §5.3）——此处锁定调用点。"""
    monkeypatch.setattr(C, "connect", lambda: conn)
    monkeypatch.delenv("WEBHOOK_URL", raising=False)   # send_summary 静默跳过
    monkeypatch.setattr(C, "backup_database", lambda c: None)
    events = []

    def fake_snap(c, target, now):
        events.append(("snap", target))
        return 0

    def fake_fetch(fetcher, trade_date):
        events.append(("fetch", fetcher.source_id, trade_date))
        return []

    monkeypatch.setattr(C, "collect_market_snapshot", fake_snap)
    monkeypatch.setattr(C, "fetch_with_retry", fake_fetch)
    C.main(["--date", "2026-10-16"])   # 周五 → 目标日=前一工作日周四
    assert ("snap", "2026-10-15") in events
    first_fetch = min(i for i, e in enumerate(events) if e[0] == "fetch")
    assert events.index(("snap", "2026-10-15")) < first_fetch
    assert sum(1 for e in events if e[0] == "snap") == 1


def test_main_daily_with_source_skips_snapshot(conn, monkeypatch):
    # --source 调试/单源补跑不触快照（与既有 main() 测试不触网的隔离口径一致）
    monkeypatch.setattr(C, "connect", lambda: conn)
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    monkeypatch.setattr(C, "backup_database", lambda c: None)
    monkeypatch.setattr(C, "fetch_with_retry", lambda f, d: [])
    calls = []
    monkeypatch.setattr(C, "collect_market_snapshot",
                        lambda c, t, n: calls.append(t))
    C.main(["--date", "2026-10-16", "--source", "hs300-etf"])
    assert calls == []


def test_main_backfill_snapshot_prepass_with_coverage(conn, monkeypatch):
    """backfill 预跑快照历史（同 target 语义=前一交易日，一次回补即得全史）；
       已有快照的日期按日覆盖门控跳过——重跑零开销。"""
    monkeypatch.setattr(C, "connect", lambda: conn)
    monkeypatch.setattr(C, "trade_dates_from_to",
                        lambda a, b: ["2026-01-05", "2026-01-06"])
    monkeypatch.setattr(C, "fetch_with_retry", lambda f, d: [])
    calls = []

    def fake_snap(c, target, now):
        calls.append(target)
        c.execute("INSERT OR REPLACE INTO etf_snapshots VALUES (?,?,?,?,?,?)",
                  (target, "000001", "标记", "SSE", 1.0, now))
        c.commit()
        return 1

    monkeypatch.setattr(C, "collect_market_snapshot", fake_snap)
    argv = ["--backfill", "--from", "2026-01-05", "--to", "2026-01-06",
            "--source", "etf-watchlist"]
    assert C.main(argv) == 0
    assert calls == ["2026-01-02", "2026-01-05"]   # 01-05(一)→01-02(五)；01-06(二)→01-05(一)
    assert C.main(argv) == 0
    assert calls == ["2026-01-02", "2026-01-05"]   # 第二次全命中覆盖门控，零调用


def test_main_backfill_other_source_skips_snapshot(conn, monkeypatch):
    monkeypatch.setattr(C, "connect", lambda: conn)
    monkeypatch.setattr(C, "trade_dates_from_to",
                        lambda a, b: ["2026-01-05", "2026-01-06"])
    monkeypatch.setattr(C, "fetch_with_retry", lambda f, d: [])
    calls = []
    monkeypatch.setattr(C, "collect_market_snapshot",
                        lambda c, t, n: calls.append(t))
    assert C.main(["--backfill", "--from", "2026-01-05", "--to", "2026-01-06",
                   "--source", "hs300-etf"]) == 0
    assert calls == []


# ---------- LocalSnapshotFetcher ----------

@pytest.fixture
def snap_env(tmp_path, monkeypatch):
    """fetcher 自开连接（db_path()），须先设 DB_PATH 再建库。
       instruments 用未入种子的代码（159516/159901/513100）——510300 等
       已属其他源，instruments.id 唯一约束会拒插。"""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "snap.db"))
    c = db.connect()
    db.init_db(c)
    c.executemany(
        "INSERT INTO instruments(id,source_id,code,metric,unit,name,sort_order)"
        " VALUES (?,?,?,?,?,?,?)", [
            ("159516", "etf-watchlist", "159516", "share", "亿份", "创业板50ETF", 1),
            ("159901", "etf-watchlist", "159901", "share", "亿份", "深100ETF", 2),
            ("513100", "etf-watchlist", "513100", "share", "亿份", "纳指ETF", 3),
        ])
    c.executemany("INSERT INTO etf_snapshots VALUES (?,?,?,?,?,?)", [
        ("2026-10-15", "159516", "创业板50ETF", "SZSE", 100.0, "t"),
        ("2026-10-15", "159901", "深100ETF", "SZSE", 50.0, "t"),
        ("2026-10-15", "999999", "未注册代码", "SSE", 9.0, "t"),
        ("2026-10-15", "513100", "纳指ETF", "SSE", None, "t"),
        ("2026-10-14", "159516", "创业板50ETF", "SZSE", 99.0, "t"),
    ])
    c.commit()
    yield c
    c.close()


def _watch_fetcher(c):
    src = c.execute("SELECT * FROM sources WHERE id='etf-watchlist'").fetchone()
    insts = c.execute(
        "SELECT * FROM instruments WHERE source_id='etf-watchlist'"
        " ORDER BY sort_order").fetchall()
    return build_fetcher(src["fetcher"], src, insts)   # seed 名必须已注册（KeyError 即败）


def test_local_snapshot_maps_to_observations(snap_env):
    obs = _watch_fetcher(snap_env).fetch("2026-10-15")
    by = {o.instrument_id: o for o in obs}
    assert set(by) == {"159516", "159901"}   # 999999 未注册、513100 空份额均跳过
    assert by["159516"].metric == "share"
    assert by["159516"].value == 100.0
    assert by["159516"].trade_date == "2026-10-15"
    assert by["159901"].value == 50.0


def test_local_snapshot_exact_date_only(snap_env):
    # 只取请求日快照（历史已在注册时回填，fetcher 不越日取数）
    obs = _watch_fetcher(snap_env).fetch("2026-10-14")
    assert [(o.instrument_id, o.value) for o in obs] == [("159516", 99.0)]


def test_local_snapshot_no_targets(snap_env):
    src = snap_env.execute(
        "SELECT * FROM sources WHERE id='etf-watchlist'").fetchone()
    assert build_fetcher(src["fetcher"], src, []).fetch("2026-10-15") == []


def test_local_snapshot_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "nope.db"))   # 不 init_db
    assert LocalSnapshotFetcher({}, []).fetch("2026-10-15") == []


def test_local_snapshot_contract(snap_env):
    f = _watch_fetcher(snap_env)
    assert f.source_id == "etf-watchlist" and f.lag_days == 1
