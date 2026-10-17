"""v1.3 资金流 API 单测：GET /api/flows（全市场快照环比）+ POST /api/watchlist
   （一键注册 + 快照表历史回填，幂等）。"""
import pytest
from fastapi.testclient import TestClient

from backend.app import db
from backend.app.main import app


@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    # lifespan 会在 DB_PATH 上 connect+init_db——指到 tmp 防误触开发库
    monkeypatch.setenv("DB_PATH", str(tmp_path / "startup.db"))
    app.dependency_overrides[db.get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def snaps(conn):
    """两日 × 多品种：510300 沪 100→103；159915 深 50→45；
       588000/159901 持平（change=0：符号过滤后不上任何榜）；
       159516 仅末日（无前值，不进变动榜但计入 count）。"""
    conn.executemany("INSERT INTO etf_snapshots VALUES (?,?,?,?,?,?)", [
        ("2026-10-14", "510300", "沪深300ETF", "SSE", 100.0, "t"),
        ("2026-10-14", "159915", "创业板ETF", "SZSE", 50.0, "t"),
        ("2026-10-14", "588000", "科创50ETF", "SSE", 200.0, "t"),
        ("2026-10-14", "159901", "深100ETF", "SZSE", 10.0, "t"),
        ("2026-10-15", "510300", "沪深300ETF", "SSE", 103.0, "t"),
        ("2026-10-15", "159915", "创业板ETF", "SZSE", 45.0, "t"),
        ("2026-10-15", "588000", "科创50ETF", "SSE", 200.0, "t"),
        ("2026-10-15", "159901", "深100ETF", "SZSE", 10.0, "t"),
        ("2026-10-15", "159516", "创业板50ETF", "SZSE", 25.5, "t"),
    ])
    conn.commit()


# ---------- GET /api/flows ----------

def test_flows_default(client, snaps):
    r = client.get("/api/flows").json()
    assert r["as_of_date"] == "2026-10-15"
    assert r["count"] == 5                     # 当日快照全部代码（含无前值的 159516）
    assert r["total_change"] == pytest.approx(-2.0)   # +3 -5 +0 +0
    # 榜按符号过滤：top_in 只收 change>0、top_out 只收 change<0；
    # 持平代码（159901/588000）与单边日的零变动行不得填满反向榜
    assert [x["code"] for x in r["top_in"]] == ["510300"]
    assert [x["code"] for x in r["top_out"]] == ["159915"]
    top = r["top_in"][0]
    assert top == {"code": "510300", "name": "沪深300ETF", "exchange": "SSE",
                   "shares": 103.0, "change": 3.0, "pct": 3.0}
    assert r["top_out"][0]["change"] == -5.0
    assert r["top_out"][0]["pct"] == pytest.approx(-10.0)


def test_flows_limit(client, snaps):
    r = client.get("/api/flows", params={"limit": 1}).json()
    assert [x["code"] for x in r["top_in"]] == ["510300"]
    assert [x["code"] for x in r["top_out"]] == ["159915"]


def test_flows_one_sided_day(client, conn):
    """单边日：全市场净申购 → top_out 为空；全市场净赎回 → top_in 为空
       （反向榜不得被正/负变动之外的行填充）。"""
    def ins(d, vals):
        conn.executemany(
            "INSERT OR REPLACE INTO etf_snapshots VALUES (?,?,?,?,?,?)",
            [(d, c, f"ETF{c}", "SSE" if c[0] == "5" else "SZSE", v, "t")
             for c, v in vals.items()])
        conn.commit()

    ins("2026-10-20", {"510300": 100.0, "159915": 50.0, "588000": 200.0})
    ins("2026-10-21", {"510300": 110.0, "159915": 55.0, "588000": 220.0})
    r = client.get("/api/flows", params={"date": "2026-10-21"}).json()
    assert r["count"] == 3
    assert r["total_change"] == pytest.approx(35.0)   # +10 +5 +20
    assert [x["code"] for x in r["top_in"]] == ["588000", "510300", "159915"]
    assert r["top_out"] == []                          # 净申购日无赎回榜
    ins("2026-10-22", {"510300": 90.0, "159915": 40.0, "588000": 180.0})
    r = client.get("/api/flows", params={"date": "2026-10-22"}).json()
    assert [x["code"] for x in r["top_out"]] == ["588000", "510300", "159915"]
    assert r["top_in"] == []                           # 净赎回日无申购榜


def test_flows_first_day_no_prev(client, snaps):
    # 首日无前值：变动榜与合计为空，但 count 照常给出（全市场覆盖面）
    r = client.get("/api/flows", params={"date": "2026-10-14"}).json()
    assert r["as_of_date"] == "2026-10-14"
    assert r["count"] == 4
    assert r["total_change"] is None
    assert r["top_in"] == [] and r["top_out"] == []


def test_flows_date_without_snapshot(client, snaps):
    r = client.get("/api/flows", params={"date": "2026-10-13"}).json()
    assert r == {"as_of_date": "2026-10-13", "total_change": None,
                 "count": 0, "top_in": [], "top_out": []}


def test_flows_empty_db(client):
    r = client.get("/api/flows").json()
    assert r == {"as_of_date": None, "total_change": None,
                 "count": 0, "top_in": [], "top_out": []}


# ---------- POST /api/watchlist ----------

def test_watchlist_register_and_backfill(client, snaps, conn):
    r = client.post("/api/watchlist", json={"code": "159516"})
    assert r.status_code == 200
    body = r.json()
    assert body["registered"] is True
    assert body["name"] == "创业板50ETF"
    assert body["history_rows"] == 1
    inst = conn.execute(
        "SELECT source_id, code, metric, unit, sort_order FROM instruments"
        " WHERE id='159516'").fetchone()
    assert (inst["source_id"], inst["code"], inst["metric"],
            inst["unit"]) == ("etf-watchlist", "159516", "share", "亿份")
    assert inst["sort_order"] == 1
    obs = conn.execute(
        "SELECT trade_date, value FROM observations WHERE instrument_id='159516'"
    ).fetchall()
    assert [(o["trade_date"], o["value"]) for o in obs] == [("2026-10-15", 25.5)]


def test_watchlist_backfills_full_history_in_sort_order(client, snaps, conn):
    # 159516/159901 均未入种子（510300 等已属其他源，见跨源用例）
    assert client.post("/api/watchlist", json={"code": "159516"}).status_code == 200
    r = client.post("/api/watchlist", json={"code": "159901"}).json()
    assert r["history_rows"] == 2            # 10-14 + 10-15 两天全史
    obs = conn.execute(
        "SELECT trade_date FROM observations WHERE instrument_id='159901'"
        " ORDER BY trade_date").fetchall()
    assert [o["trade_date"] for o in obs] == ["2026-10-14", "2026-10-15"]
    assert conn.execute(
        "SELECT sort_order s FROM instruments WHERE id='159901'"
    ).fetchone()["s"] == 2                   # 排在先注册的 159516 之后


def test_watchlist_idempotent(client, snaps, conn):
    first = client.post("/api/watchlist", json={"code": "159516"}).json()
    assert first["history_rows"] == 1
    again = client.post("/api/watchlist", json={"code": "159516"}).json()
    assert again["registered"] is True
    assert again["history_rows"] == 0        # 重复注册零新增（DO NOTHING）
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE id='159516'").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM observations WHERE instrument_id='159516'"
    ).fetchone()["c"] == 1


def test_watchlist_unknown_code_404(client):
    r = client.post("/api/watchlist", json={"code": "999999"})
    assert r.status_code == 404


def test_watchlist_code_already_in_other_source(client, snaps, conn):
    # 跨源重复（510300 已属 hs300-etf）：注册语义保持简单——instrument 不迁移，
    # 历史观测照常回填且归属原源；响应仍 registered=true（幂等、不报错）
    r = client.post("/api/watchlist", json={"code": "510300"})
    assert r.status_code == 200
    body = r.json()
    assert body["registered"] is True
    assert body["history_rows"] == 2
    src = conn.execute(
        "SELECT source_id FROM instruments WHERE id='510300'").fetchone()
    assert src["source_id"] == "hs300-etf"   # 不迁移、不覆盖
