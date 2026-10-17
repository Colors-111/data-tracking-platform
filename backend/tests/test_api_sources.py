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


def test_list_sources_grouped(client, conn):
    # 造 510300 两天数据（100→103）：速览卡 total_value=103、day_change=3
    conn.execute("INSERT INTO observations VALUES('510300','share','2026-10-14',100,NULL,'t')")
    conn.execute("INSERT INTO observations VALUES('510300','share','2026-10-15',103,NULL,'t')")
    conn.commit()
    r = client.get("/api/sources")
    assert r.status_code == 200
    groups = {g["group"]: g["sources"] for g in r.json()}
    assert "指数 ETF" in groups and "大宗商品" in groups
    hs = next(s for s in groups["指数 ETF"] if s["id"] == "hs300-etf")
    assert hs["name"] == "沪深300 ETF" and hs["unit"] == "亿份"
    assert hs["total_value"] == 103 and hs["day_change"] == 3
    # 无 observation 的源：合计字段为 NULL 而非报错
    coal = next(s for s in groups["大宗商品"] if s["id"] == "coal-price")
    assert coal["total_value"] is None and coal["day_change"] is None


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


def test_source_detail_instrument_meta(client, conn):
    # v1.2 增量字段：cards 每张带 metric/unit（NULL instrument.unit 回退 source.unit），
    # 顶层 instrument_meta 含全部 instruments（含无数据的，供前端分区展示"暂无数据"区）
    for d, v in [("2026-10-14", 100), ("2026-10-15", 103)]:
        conn.execute("INSERT INTO observations VALUES('510300','share',?,?,NULL,'t')", (d, v))
    conn.commit()
    body = client.get("/api/sources/hs300-etf?range=1m").json()
    card = body["cards"][0]
    assert card["metric"] == "share" and card["unit"] == "亿份"   # NULL instrument.unit 回退源单位
    meta = body["instrument_meta"]["510300"]
    assert meta == {"metric": "share", "unit": "亿份", "name": "沪深300ETF华泰柏瑞"}
    # series 形状不变（Task 13-17 契约）
    assert body["series"]["510300"] == [["2026-10-14", 100], ["2026-10-15", 103]]


def test_source_detail_commodity_meta(client, conn):
    # 商品组源：instrument 级 unit（不回退源 unit='混合'）；meta 覆盖无数据的 instrument
    conn.execute("INSERT INTO observations VALUES('cu-shfe-price','price','2026-10-14',70000,NULL,'t')")
    conn.execute("INSERT INTO observations VALUES('cu-shfe-price','price','2026-10-15',77000,NULL,'t')")
    conn.commit()
    body = client.get("/api/sources/copper?range=1m").json()
    assert set(body["instrument_meta"]) == {
        "cu-shfe-price", "cu-spot-price", "cu-inventory", "cu-warrant",
        "cu-lme-stock", "cu-lme-warrant", "cu-lme-holding"}
    assert body["instrument_meta"]["cu-shfe-price"] == {
        "metric": "price", "unit": "元/吨", "name": "SHFE沪铜主力"}
    assert body["instrument_meta"]["cu-warrant"]["metric"] == "warehouse"
    assert body["instrument_meta"]["cu-warrant"]["unit"] == "吨"   # 品种级 unit 不被源 unit='混合' 覆盖
    card = next(c for c in body["cards"] if c["instrument_id"] == "cu-shfe-price")
    assert card["metric"] == "price" and card["unit"] == "元/吨"
    # 无数据的 instrument 仍进 meta，但不产生 card（既有行为）
    assert "cu-warrant" not in {c["instrument_id"] for c in body["cards"]}
    assert body["series"]["cu-warrant"] == []


def test_list_sources_mixed_unit_null_totals(client, conn):
    # v1.2 综合审查：unit='混合' 的商品组源跨量纲求和无意义（元/克+美元/盎司+吨+张+手）
    # → total_value/day_change 置 NULL；单一位 ETF 源照旧求和
    for iid, metric, d, v in [("cu-shfe-price", "price", "2026-10-14", 70000),
                              ("cu-shfe-price", "price", "2026-10-15", 77000),
                              ("cu-inventory", "inventory", "2026-10-14", 1000),
                              ("cu-inventory", "inventory", "2026-10-15", 1200),
                              ("510300", "share", "2026-10-14", 100),
                              ("510300", "share", "2026-10-15", 103)]:
        conn.execute("INSERT INTO observations VALUES(?,?,?,?,NULL,'t')", (iid, metric, d, v))
    conn.commit()
    groups = {g["group"]: g["sources"] for g in client.get("/api/sources").json()}
    copper = next(s for s in groups["大宗商品"] if s["id"] == "copper")
    assert copper["total_value"] is None and copper["day_change"] is None
    assert copper["latest_date"] == "2026-10-15"   # 有数据，只是不求和
    hs = next(s for s in groups["指数 ETF"] if s["id"] == "hs300-etf")
    assert hs["total_value"] == 103 and hs["day_change"] == 3   # ETF 求和契约不变


def test_unknown_source_404(client):
    assert client.get("/api/sources/nope").status_code == 404


def test_bad_range_400(client):
    assert client.get("/api/sources/hs300-etf?range=2m").status_code == 400


def test_custom_window_overrides_bad_range(client, conn):
    # 口径与 /api/series 统一：给了自定义起止时窗口完全由 start/end 决定，
    # range 档位被忽略、不再做白名单校验（range=banana + start → 200）
    conn.execute("INSERT INTO observations VALUES('510300','share','2026-10-15',103,NULL,'t')")
    conn.commit()
    r = client.get("/api/sources/hs300-etf?range=banana&start=2026-01-01&end=2026-12-31")
    assert r.status_code == 200
    assert [p[0] for p in r.json()["series"]["510300"]] == ["2026-10-15"]
    # 无自定义窗口时坏 range 仍 400（既有契约不动）
    assert client.get("/api/sources/hs300-etf?range=banana").status_code == 400


def test_empty_source_returns_null_shape(client):
    # coal-price 有源有 instrument 但无 observation
    r = client.get("/api/sources/coal-price")
    assert r.status_code == 200
    body = r.json()
    assert body["as_of_date"] is None
    assert body["cards"] == []
    assert body["series"] == {}
    # v1.2 增量：空源也带 instrument_meta（前端分区可展示"数据累积中"），unit 回退源单位
    assert body["instrument_meta"] == {
        "coal-price": {"metric": "price", "unit": "元/吨", "name": "动力煤现货价"}}


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


def test_detail_range_3y_5y(client, conn):
    # 造横跨 6 年的数据：as_of=2026-10-15；
    # 5y 下界 = date(as_of,'-1827 days') = 2021-10-14，3y 下界 = date(as_of,'-1096 days') = 2023-10-15
    for d, v in [("2021-06-01", 90), ("2022-01-01", 100),
                 ("2023-06-01", 105), ("2024-01-01", 110), ("2026-10-15", 120)]:
        conn.execute("INSERT INTO observations VALUES('510300','share',?,?,NULL,'t')", (d, v))
    conn.commit()
    r5 = client.get("/api/sources/hs300-etf?range=5y").json()
    assert [p[0] for p in r5["series"]["510300"]] == \
        ["2022-01-01", "2023-06-01", "2024-01-01", "2026-10-15"]   # 2021-06-01 在 5y 窗口外
    r3 = client.get("/api/sources/hs300-etf?range=3y").json()
    assert [p[0] for p in r3["series"]["510300"]] == ["2024-01-01", "2026-10-15"]


def test_detail_custom_start_end(client, conn):
    for d, v in [("2015-01-05", 100), ("2020-06-01", 110), ("2026-10-15", 120)]:
        conn.execute("INSERT INTO observations VALUES('510300','share',?,?,NULL,'t')", (d, v))
    conn.commit()
    # 不带 range（默认 3m）+ start/end：自定义窗口覆盖 range 档位（start 优先语义）
    r = client.get("/api/sources/hs300-etf?start=2020-01-01&end=2026-01-01").json()
    assert [p[0] for p in r["series"]["510300"]] == ["2020-06-01"]
    # start 单给：end 缺省 = as_of（不截尾）
    r2 = client.get("/api/sources/hs300-etf?start=2020-01-01").json()
    assert [p[0] for p in r2["series"]["510300"]] == ["2020-06-01", "2026-10-15"]
    # end 单给：不设下界（全历史截到 end）
    r3 = client.get("/api/sources/hs300-etf?end=2026-01-01").json()
    assert [p[0] for p in r3["series"]["510300"]] == ["2015-01-05", "2020-06-01"]
    # 非法日期格式 → 400
    assert client.get("/api/sources/hs300-etf?start=banana").status_code == 400
    assert client.get("/api/sources/hs300-etf?end=nope").status_code == 400
    # start 晚于 end → 400
    assert client.get("/api/sources/hs300-etf?start=2026-01-01&end=2020-01-01").status_code == 400
