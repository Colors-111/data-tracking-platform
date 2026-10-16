import pytest
from fastapi.testclient import TestClient
from backend.app.main import app


@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    # startup 初始化指向临时库，测试不碰真实 data/dtp.db
    monkeypatch.setenv("DB_PATH", str(tmp_path / "startup.db"))
    # API 直接使用传入的测试连接：dependency_overrides 按函数对象覆盖
    import backend.app.db as dbm
    app.dependency_overrides[dbm.get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_list_sources_grouped(client):
    r = client.get("/api/sources")
    assert r.status_code == 200
    groups = {g["group"]: g["sources"] for g in r.json()}
    assert "指数 ETF" in groups and "大宗商品" in groups
    hs = next(s for s in groups["指数 ETF"] if s["id"] == "hs300-etf")
    assert hs["name"] == "沪深300 ETF" and hs["unit"] == "亿份"


def test_source_detail_shape(client, conn):
    # 造 3 天数据
    for i, (d, v) in enumerate([("2026-10-13", 100), ("2026-10-14", 101), ("2026-10-15", 103)]):
        conn.execute("INSERT INTO observations VALUES('510300','share',?,?,NULL,'t')", (d, v))
    conn.commit()
    r = client.get("/api/sources/hs300-etf?range=1m")
    body = r.json()
    assert body["as_of_date"] == "2026-10-15"
    card = next(c for c in body["cards"] if c["instrument_id"] == "510300")
    assert card["value"] == 103 and card["day_change"] == 2 and card["day_pct"] == pytest.approx(1.979, abs=0.01)
    assert len(body["series"]["510300"]) == 3


def test_unknown_source_404(client):
    assert client.get("/api/sources/nope").status_code == 404


def test_bad_range_400(client):
    assert client.get("/api/sources/hs300-etf?range=2m").status_code == 400


def test_empty_source_returns_null_shape(client):
    # coal-price 有源有 instrument 但无 observation
    r = client.get("/api/sources/coal-price")
    assert r.status_code == 200
    body = r.json()
    assert body["as_of_date"] is None
    assert body["cards"] == []
    assert body["series"] == {}


def test_window_filter_excludes_old_rows(client, conn):
    # 一条窗口外老数据 + 3 条窗口内：range=1m 的 day_filter 必须真过滤
    for d, v in [("2020-01-01", 1), ("2026-10-13", 100), ("2026-10-14", 101), ("2026-10-15", 103)]:
        conn.execute("INSERT INTO observations VALUES('510300','share',?,?,NULL,'t')", (d, v))
    conn.commit()
    r = client.get("/api/sources/hs300-etf?range=1m")
    body = r.json()
    pts = body["series"]["510300"]
    assert len(pts) == 3
    assert [p[0] for p in pts] == ["2026-10-13", "2026-10-14", "2026-10-15"]
