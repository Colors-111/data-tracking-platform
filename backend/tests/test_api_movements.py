import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    # startup 初始化指向临时库，测试不碰真实 data/dtp.db（照抄 test_api_sources.py 模式）
    monkeypatch.setenv("DB_PATH", str(tmp_path / "startup.db"))
    # API 直接使用传入的测试连接：dependency_overrides 按函数对象覆盖
    import backend.app.db as dbm
    app.dependency_overrides[dbm.get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(conn):
    # 510300: 100 -> 103 (+3% ≥ 阈值1%)；510310: 50 -> 50.2 (+0.4% < 阈值)
    for iid, d, v in [("510300", "2026-10-14", 100), ("510300", "2026-10-15", 103),
                      ("510310", "2026-10-14", 50), ("510310", "2026-10-15", 50.2),
                      ("510330", "2026-10-15", 86.2)]:
        conn.execute("INSERT INTO observations VALUES(?,?,?,?,NULL,'t')", (iid, "share", d, v))
    conn.commit()


def test_movements_filters_by_threshold(client, seeded):
    r = client.get("/api/movements")
    assert r.status_code == 200
    items = r.json()["movements"]
    assert len(items) == 1
    m = items[0]
    assert m["instrument_id"] == "510300"
    assert m["pct"] == pytest.approx(3.0)
    assert m["source_name"] == "沪深300 ETF"


def test_movements_desc_by_abs_pct(client, conn):
    for iid, d, v in [("510300", "2026-10-14", 100), ("510300", "2026-10-15", 110),
                      ("510310", "2026-10-14", 50), ("510310", "2026-10-15", 50.6)]:
        conn.execute("INSERT INTO observations VALUES(?,?,?,?,NULL,'t')", (iid, "share", d, v))
    conn.commit()
    items = client.get("/api/movements").json()["movements"]
    assert [m["instrument_id"] for m in items] == ["510300", "510310"]


def test_movements_empty_db(client):
    body = client.get("/api/movements").json()
    assert body == {"as_of_date": None, "movements": []}


def test_movements_date_param_path(client, seeded):
    # date 参数取历史时点：10-15 行不入榜（未来于该时点）
    body = client.get("/api/movements?date=2026-10-14").json()
    assert body["as_of_date"] == "2026-10-14"
    assert body["movements"] == []   # 10-14 时 510300 只有 100 一个点，无 prev


def test_jobs_latest(client, conn):
    conn.execute("INSERT INTO job_runs(source_id,trade_date,status,rows,error,started_at,finished_at)"
                 " VALUES('hs300-etf','2026-10-15','ok',5,NULL,'t','t')")
    conn.execute("INSERT INTO job_runs(source_id,trade_date,status,rows,error,started_at,finished_at)"
                 " VALUES('cu-inventory','2026-10-15','failed',0,'RuntimeError()','t','t')")
    conn.commit()
    body = client.get("/api/jobs/latest").json()
    statuses = {j["source_id"]: j["status"] for j in body["jobs"]}
    assert statuses == {"hs300-etf": "ok", "cu-inventory": "failed"}
