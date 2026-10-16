import pytest
from backend.app import db
from backend.fetchers.base import Observation, register
from backend import collect as C

@pytest.fixture
def fake_source(conn):
    conn.execute("INSERT OR REPLACE INTO sources VALUES"
                 "('fake','假源','指数 ETF','daily','fake.ok','x',1.0,0,1,99)")
    conn.execute("INSERT OR REPLACE INTO instruments VALUES"
                 "('f1','fake','F1','price','假品种',1)")
    conn.commit()

@register("fake.ok")
class OkFetcher:
    source_id = "fake"
    lag_days = 1   # 与真实 T+1 源一致；回补测试的目标日链（01-05→01-02）依赖它
    def __init__(self, source, instruments): pass
    def fetch(self, trade_date):
        return [Observation("f1", "price", trade_date, 42.0)]

@register("fake.boom")
class BoomFetcher:
    source_id = "fake"
    lag_days = 0
    def __init__(self, source, instruments): pass
    def fetch(self, trade_date):
        raise RuntimeError("接口爆炸")

def _run(conn, src_id="fake", **kw):
    src = conn.execute("SELECT * FROM sources WHERE id=?", (src_id,)).fetchone()
    insts = conn.execute("SELECT * FROM instruments WHERE source_id=?", (src_id,)).fetchall()
    return C.run_source(conn, src, insts, "2026-10-16", **kw)

@pytest.fixture(autouse=True)
def _no_calendar(monkeypatch):
    """单元测试不触网：交易日历强制走工作日降级；需要真实日历的测试可在用例内覆盖"""
    monkeypatch.setattr(C, "_trade_days", lambda: None)

def test_run_source_ok_and_idempotent(conn, fake_source):
    r1 = _run(conn)
    assert r1["status"] == "ok" and r1["rows"] == 1
    n1 = conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
    _run(conn)  # 重跑
    n2 = conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
    assert n1 == n2 == 1          # 幂等
    assert conn.execute("SELECT COUNT(*) c FROM job_runs").fetchone()["c"] == 2

def test_run_source_failure_isolated(conn, fake_source):
    conn.execute("UPDATE sources SET fetcher='fake.boom' WHERE id='fake'"); conn.commit()
    r = _run(conn)
    assert r["status"] == "failed" and "接口爆炸" in r["error"]
    assert conn.execute(
        "SELECT status FROM job_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()["status"] == "failed"

def test_run_source_empty(conn, fake_source):
    @register("fake.empty")
    class E:
        source_id = "fake"; lag_days = 0
        def __init__(self, source, instruments): pass
        def fetch(self, trade_date): return []
    conn.execute("UPDATE sources SET fetcher='fake.empty' WHERE id='fake'"); conn.commit()
    assert _run(conn)["status"] == "empty"

def test_target_date_respects_lag(conn, fake_source, monkeypatch):
    seen = {}
    @register("fake.lag")
    class L:
        source_id = "fake"; lag_days = 1
        def __init__(self, source, instruments): pass
        def fetch(self, trade_date):
            seen["d"] = trade_date
            return [Observation("f1", "price", trade_date, 1.0)]
    conn.execute("UPDATE sources SET fetcher='fake.lag' WHERE id='fake'"); conn.commit()
    _run(conn)
    assert seen["d"] == "2026-10-15"   # 2026-10-16 为周五，降级取前 1 个工作日=周四

def test_previous_trade_date_calendar(monkeypatch):
    days = ("2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")
    monkeypatch.setattr(C, "_trade_days", lambda: days)   # 覆盖 autouse 的降级
    assert C.previous_trade_date("2026-10-17", 1) == "2026-10-16"  # 周六→周五
    assert C.previous_trade_date("2026-10-16", 2) == "2026-10-14"  # 前 2 个交易日
    assert C.previous_trade_date("2026-10-13", 1) == "2026-10-13"  # 边界钳制到首个

def test_previous_trade_date_fallback_skips_weekend():
    assert C.previous_trade_date("2026-10-12", 1) == "2026-10-09"  # 周一→上周五（autouse 降级）

def test_backfill_break_on_failure_and_resume(conn, fake_source, monkeypatch):
    # main() 自己 connect()——替换为测试 fixture 的连接，断言才能看到写入
    monkeypatch.setattr(C, "connect", lambda: conn)
    monkeypatch.setattr(C, "trade_dates_from_to",
                        lambda a, b: ["2026-01-05", "2026-01-06", "2026-01-07"])

    @register("fake.flaky")
    class Flaky:
        source_id = "fake"; lag_days = 1
        def __init__(self, source, instruments): pass
        def fetch(self, trade_date):
            if trade_date == "2026-01-05":  # 第2天(run_date=01-06)的目标日(lag1)=01-05 时失败
                raise RuntimeError("半路炸")
            return [Observation("f1", "price", trade_date, 42.0)]

    conn.execute("UPDATE sources SET fetcher='fake.flaky' WHERE id='fake'"); conn.commit()
    rc = C.main(["--backfill", "--from", "2026-01-05", "--to", "2026-01-07",
                 "--source", "fake"])
    assert rc == 1                                    # 有失败 → 非零退出
    statuses = conn.execute(
        "SELECT trade_date, status FROM job_runs ORDER BY id").fetchall()
    assert [(r["trade_date"], r["status"]) for r in statuses] == [
        ("2026-01-05", "ok"), ("2026-01-06", "failed")]   # 第3天未执行（break）

    # 修复后续跑：整段补齐
    conn.execute("UPDATE sources SET fetcher='fake.ok' WHERE id='fake'"); conn.commit()
    rc = C.main(["--backfill", "--from", "2026-01-05", "--to", "2026-01-07",
                 "--source", "fake"])
    assert rc == 0
    targets = {r["trade_date"] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM observations")}
    assert targets == {"2026-01-02", "2026-01-05", "2026-01-06"}  # 断点续跑补齐

def test_backup_database_creates_snapshot_and_rotates(conn, fake_source, monkeypatch, tmp_path):
    import datetime as dt
    dbfile = tmp_path / "dtp.db"
    monkeypatch.setenv("DB_PATH", str(dbfile))
    _run(conn)  # 造点数据
    dest = C.backup_database(conn)
    assert dest and (tmp_path / "backups" / f"dtp-{dt.date.today().isoformat()}.db").exists()
    assert C.backup_database(conn) == dest          # 同日重复备份幂等
    bdir = tmp_path / "backups"
    for i in range(31):                             # 造 31 个旧备份
        (bdir / f"dtp-2020-01-{i+1:02d}.db").write_text("x")
    C.backup_database(conn)
    remaining = sorted(p.name for p in bdir.glob("dtp-*.db"))
    assert len(remaining) == 30 and "dtp-2020-01-01.db" not in remaining  # 滚动保留 30 份

def test_main_backup_gate(conn, fake_source, monkeypatch):
    """备份门控契约：main() 全成功备份 1 次；failed 不备份（0 次）。"""
    monkeypatch.setattr(C, "connect", lambda: conn)
    monkeypatch.setattr(C, "fetch_with_retry", lambda f, d: f.fetch(d))  # 跳过重试等待
    monkeypatch.delenv("WEBHOOK_URL", raising=False)   # send_summary 无 webhook 时静默跳过
    calls = []
    monkeypatch.setattr(C, "backup_database", lambda c: calls.append(c))
    assert C.main(["--source", "fake"]) == 0
    assert len(calls) == 1          # 全成功 → 备份 1 次
    calls.clear()
    conn.execute("UPDATE sources SET fetcher='fake.boom' WHERE id='fake'"); conn.commit()
    assert C.main(["--source", "fake"]) == 1
    assert len(calls) == 0          # failed → 不备份

def test_backfill_fills_history_before_recent_data(conn, fake_source, monkeypatch):
    # 库里已有"新"数据（target=01-06）时，--backfill 仍须补更早历史（01-02、01-05）；
    # 已覆盖的 target（01-06）按日期精确跳过，不重复 fetch
    monkeypatch.setattr(C, "connect", lambda: conn)
    monkeypatch.setattr(C, "trade_dates_from_to",
                        lambda a, b: ["2026-01-05", "2026-01-06", "2026-01-07"])
    conn.execute("INSERT INTO observations VALUES"
                 "('f1','price','2026-01-06',99.0,NULL,'2026-01-07T09:00:00')")
    conn.commit()
    rc = C.main(["--backfill", "--from", "2026-01-05", "--to", "2026-01-07",
                 "--source", "fake"])
    assert rc == 0
    targets = {r["trade_date"] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM observations")}
    assert targets == {"2026-01-02", "2026-01-05", "2026-01-06"}  # 01-06 预插；01-02/01-05 补历史
    ran = {r["trade_date"] for r in conn.execute(
        "SELECT trade_date FROM job_runs")}
    assert ran == {"2026-01-05", "2026-01-06"}   # d=01-07 的 target 已覆盖 → 未 fetch

def test_backfill_window_fetcher_stops_early(conn, fake_source, monkeypatch):
    # 窗口型源（fetch 忽略目标日，恒返回近期整段）：首次 fetch 后即 break，不空跑整个区间
    monkeypatch.setattr(C, "connect", lambda: conn)
    monkeypatch.setattr(C, "trade_dates_from_to",
                        lambda a, b: ["2026-01-05", "2026-01-06", "2026-01-07"])
    calls = []

    @register("fake.window")
    class Window:
        source_id = "fake"; lag_days = 1
        def __init__(self, source, instruments): pass
        def fetch(self, trade_date):
            calls.append(trade_date)
            return [Observation("f1", "price", "2026-01-06", 42.0)]  # 固定近期，不含目标日

    conn.execute("UPDATE sources SET fetcher='fake.window' WHERE id='fake'"); conn.commit()
    rc = C.main(["--backfill", "--from", "2026-01-05", "--to", "2026-01-07",
                 "--source", "fake"])
    assert rc == 0                                  # 提前停止不算失败
    assert len(calls) == 1                          # 首次 fetch 即 break
    assert {r["trade_date"] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM observations")} == {"2026-01-06"}
    assert conn.execute("SELECT COUNT(*) c FROM job_runs").fetchone()["c"] == 1
