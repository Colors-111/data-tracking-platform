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
def cross_source(conn):
    # 510300(share) 与 coal-price(price)，量纲不同
    for iid, d, v in [("510300", "2026-10-13", 100), ("510300", "2026-10-14", 110), ("510300", "2026-10-15", 120),
                      ("coal-price", "2026-10-13", 800), ("coal-price", "2026-10-14", 820), ("coal-price", "2026-10-15", 780)]:
        conn.execute("INSERT INTO observations VALUES(?,?,?,?,NULL,'t')",
                     (iid, "share" if iid.startswith("5") else "price", d, v))
    conn.commit()


def test_series_raw(client, cross_source):
    body = client.get("/api/series?instruments=510300,coal-price&range=1m").json()
    assert body["as_of_date"] == "2026-10-15"
    assert len(body["series"]) == 2
    s = next(s for s in body["series"] if s["instrument_id"] == "510300")
    assert s["points"][-1] == ["2026-10-15", 120]


def test_series_normalized_to_100(client, cross_source):
    body = client.get("/api/series?instruments=510300,coal-price&range=1m&normalize=1").json()
    for s in body["series"]:
        assert s["points"][0][1] == pytest.approx(100.0)
    hs = next(s for s in body["series"] if s["instrument_id"] == "510300")
    assert hs["points"][-1][1] == pytest.approx(120.0)
    coal = next(s for s in body["series"] if s["instrument_id"] == "coal-price")
    assert coal["points"][-1][1] == pytest.approx(97.5)  # 780/800*100——尾点跌破基线的负增长路径（510300 为上涨端点 120.0）


def test_series_expands_source_id(client, cross_source):
    # 传入 source id 自动展开为该源全部 instruments（Task 17 对比 chips 依赖）
    body = client.get("/api/series?instruments=hs300-etf&range=1m").json()
    assert body["as_of_date"] == "2026-10-15"
    assert len(body["series"]) == 1   # 只有 510300 有数据，其余 4 只空序列不出现
    assert body["series"][0]["instrument_id"] == "510300"


@pytest.fixture
def span_years(conn):
    # 横跨 6 年的单 instrument 数据：as_of=2026-10-15；
    # 5y 下界 = 2021-10-14（-1827 天），3y 下界 = 2023-10-15（-1096 天）
    for d, v in [("2021-06-01", 90), ("2022-01-01", 100),
                 ("2023-06-01", 105), ("2024-01-01", 110), ("2026-10-15", 120)]:
        conn.execute("INSERT INTO observations VALUES('510300','share',?,?,NULL,'t')", (d, v))
    conn.commit()


def test_series_range_3y_5y(client, span_years):
    r5 = client.get("/api/series?instruments=510300&range=5y").json()
    assert [p[0] for p in r5["series"][0]["points"]] == \
        ["2022-01-01", "2023-06-01", "2024-01-01", "2026-10-15"]   # 2021-06-01 在 5y 窗口外
    r3 = client.get("/api/series?instruments=510300&range=3y").json()
    assert [p[0] for p in r3["series"][0]["points"]] == ["2024-01-01", "2026-10-15"]


def test_series_custom_start_end(client, span_years):
    # 自定义窗口覆盖 range 档位：range 默认 6m，start/end 同时给 → start 优先
    body = client.get("/api/series?instruments=510300&start=2022-06-01&end=2025-01-01").json()
    assert [p[0] for p in body["series"][0]["points"]] == ["2023-06-01", "2024-01-01"]
    # normalize 与自定义窗口叠加：基线 = 窗口内首个非空值（105）
    nb = client.get("/api/series?instruments=510300&start=2022-06-01&end=2025-01-01&normalize=1").json()
    pts = nb["series"][0]["points"]
    assert pts[0][1] == pytest.approx(100.0)
    assert pts[-1][1] == pytest.approx(110 / 105 * 100, abs=0.01)
    # 非法日期格式 / start 晚于 end → 400
    assert client.get("/api/series?instruments=510300&start=banana").status_code == 400
    assert client.get("/api/series?instruments=510300&end=nope").status_code == 400
    assert client.get("/api/series?instruments=510300&start=2026-01-01&end=2020-01-01").status_code == 400
