"""stale 判定：库内最新日期落后于采集目标日（考虑 lag_days 已折算）。"""
import pytest
from backend.app import db

@pytest.fixture(autouse=True)
def _no_calendar(monkeypatch):
    """单元测试不触网：交易日历强制走工作日降级（同 test_collect.py）。"""
    from backend import collect as C
    monkeypatch.setattr(C, "_trade_days", lambda: None)

def _seed_pair(conn):
    conn.execute("INSERT OR REPLACE INTO sources VALUES"
                 "('v','V','g','daily','fake.stale','x',1.0,0,1,9)")
    conn.execute("INSERT OR REPLACE INTO instruments(id,source_id,code,metric,name,sort_order) VALUES"
                 "('v1','v','V1','price','v',1)")
    conn.execute("INSERT INTO observations VALUES"
                 "('v1','price','2026-10-10',1.0,NULL,'t')")
    conn.commit()

def test_stale_when_behind_target(conn):
    from backend.fetchers.base import Observation, register
    @register("fake.stale")
    class Ok:
        source_id = "v"; lag_days = 0
        def __init__(self, source, instruments): pass
        def fetch(self, td):
            return [Observation("v1", "price", "2026-10-10", 2.0)]  # 停在旧日期
    _seed_pair(conn)
    src = conn.execute("SELECT * FROM sources WHERE id='v'").fetchone()
    insts = conn.execute("SELECT * FROM instruments WHERE source_id='v'").fetchall()
    from backend import collect as C
    r = C.run_source(conn, src, insts, "2026-10-16")
    assert r["status"] == "stale"   # 目标日 10-16，库内最新 10-10
