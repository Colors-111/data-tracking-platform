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
    lag_days = 0
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

def test_target_date_respects_lag(conn, fake_source):
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
    assert seen["d"] == "2026-10-15"   # lag_days=1 → 采集目标日=交易日-1
