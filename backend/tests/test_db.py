import sqlite3
from pathlib import Path

import pytest

from backend.app import db


def test_init_db_creates_schema(conn):
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "instruments", "observations", "job_runs",
            "etf_snapshots"} <= tables


def test_wal_mode(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_seed_loaded(conn):
    # 8 原有源 - cu-inventory（并入铜组后删除独立源行）+ 4 商品组源
    # + sse50/zz1000 扩容 + v1.3 etf-watchlist（自选跟踪）+ v1.4 融资融券（margin）
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 15
    assert conn.execute(
        "SELECT COUNT(*) c FROM sources WHERE id='cu-inventory'"
    ).fetchone()["c"] == 0   # 独立源行删除：执行器不再单独跑它
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='hs300-etf'"
    ).fetchone()["c"] == 11
    # 商品组源抽查：铜 7（cu-cftc 移除——CFTC 接口无铜品种列，后续直连 COT 立项）、
    # 黄金 10（含 au-london-price 伦敦金现、au-gld-spdr SPDR 黄金 ETF 持仓）
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='copper'"
    ).fetchone()["c"] == 7
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='gold'"
    ).fetchone()["c"] == 10
    # v1.3.x 大 ETF 补齐（90% 原则）：中证500 +2、中证1000 +1、A500 +8（SQL 精算选足）
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='csi500-etf'"
    ).fetchone()["c"] == 6
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='zz1000-etf'"
    ).fetchone()["c"] == 4
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='a500-etf'"
    ).fetchone()["c"] == 11
    assert conn.execute(
        "SELECT lag_days FROM sources WHERE id='hs300-etf'"
    ).fetchone()["lag_days"] == 1


def test_watchlist_source_seeded(conn):
    # v1.3 自选跟踪源：instruments 初始为空（POST /api/watchlist 注册时补写），
    # 新分组 "自选跟踪" 经 group_name 驱动侧边栏自动出现
    row = conn.execute(
        "SELECT name, group_name, fetcher, unit, lag_days, enabled"
        " FROM sources WHERE id='etf-watchlist'").fetchone()
    assert row["name"] == "自选 ETF"
    assert row["group_name"] == "自选跟踪"
    assert row["fetcher"] == "local.snapshot"
    assert row["unit"] == "亿份"
    assert row["lag_days"] == 1 and row["enabled"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='etf-watchlist'"
    ).fetchone()["c"] == 0


def test_instruments_unit_column(conn):
    # v1.2：instruments 品种级 unit 列（源级 unit 对混合单位商品组不再够用）
    cu = conn.execute(
        "SELECT source_id, unit, metric FROM instruments WHERE id='cu-inventory'"
    ).fetchone()
    assert cu["source_id"] == "copper"   # 并入铜组，instrument id 不变 → 观测无缝保留
    assert cu["unit"] == "吨"
    assert cu["metric"] == "inventory"
    # 老 ETF instrument 的 unit 为 NULL → 前端回退到 source.unit（锁定回退语义）
    etf = conn.execute(
        "SELECT i.unit AS iu, s.unit AS su FROM instruments i"
        " JOIN sources s ON s.id = i.source_id WHERE i.id='510300'").fetchone()
    assert etf["iu"] is None
    assert etf["su"] == "亿份"
    # 商品组 instrument 带品种级单位
    assert conn.execute(
        "SELECT unit FROM instruments WHERE id='al-shfe-price'"
    ).fetchone()["unit"] == "元/吨"
    assert conn.execute(
        "SELECT unit FROM instruments WHERE id='518880'"
    ).fetchone()["unit"] == "亿份"


def test_unit_migration_on_legacy_db(tmp_path):
    # 旧 schema（instruments 无 unit 列）+ 既有观测——模拟生产库（cu-inventory 67 条观测），
    # init_db 幂等迁移后：unit 列补齐、原观测/instrument 原样保留
    c = db.connect(tmp_path / "legacy.db")
    c.executescript("""
    CREATE TABLE sources (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, group_name TEXT NOT NULL,
      freq TEXT NOT NULL DEFAULT 'daily', fetcher TEXT NOT NULL, unit TEXT,
      movement_th REAL, lag_days INTEGER NOT NULL DEFAULT 0,
      enabled INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE instruments (
      id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL REFERENCES sources(id),
      code TEXT, metric TEXT NOT NULL, name TEXT NOT NULL,
      sort_order INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE observations (
      instrument_id TEXT NOT NULL REFERENCES instruments(id),
      metric TEXT NOT NULL, trade_date TEXT NOT NULL, value REAL,
      raw_json TEXT, fetched_at TEXT NOT NULL,
      UNIQUE(instrument_id, metric, trade_date)
    );
    INSERT INTO sources VALUES('cu-inventory','电解铜库存','大宗商品','daily',
      'akshare.futures_inventory_em','吨',2.0,1,1,2);
    INSERT INTO instruments VALUES
      ('cu-inventory','cu-inventory','cu','inventory','电解铜库存',1);
    INSERT INTO observations VALUES
      ('cu-inventory','inventory','2026-10-15',123456.0,NULL,'t');
    """)
    c.commit()
    db.init_db(c)   # 迁移 + 补种，幂等
    cols = {r[1] for r in c.execute("PRAGMA table_info(instruments)")}
    assert "unit" in cols
    # 原观测无缝保留（instrument id 不变）
    obs = c.execute(
        "SELECT value FROM observations WHERE instrument_id='cu-inventory'").fetchall()
    assert len(obs) == 1 and obs[0]["value"] == 123456.0
    # instrument 迁入铜组并补单位；独立源行删除；其余种子照常补齐
    row = c.execute(
        "SELECT source_id, unit FROM instruments WHERE id='cu-inventory'").fetchone()
    assert (row["source_id"], row["unit"]) == ("copper", "吨")
    assert c.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 15
    c.close()


def test_observation_unique_upsert(conn):
    conn.execute(
        "INSERT INTO observations VALUES('510300','share','2026-10-15',100.5,NULL,'2026-10-16T17:00')")
    conn.execute(
        "INSERT OR REPLACE INTO observations VALUES('510300','share','2026-10-15',101.0,NULL,'2026-10-16T17:01')")
    row = conn.execute(
        "SELECT value FROM observations WHERE instrument_id='510300'").fetchone()
    assert row["value"] == 101.0  # 唯一键冲突走 OR REPLACE，行数不变
    assert conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"] == 1


def test_observation_fk_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO observations VALUES('nope','share','2026-01-01',1,NULL,'t')")


def test_init_db_idempotent(conn):
    db.init_db(conn)
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 15


def test_seed_backfill_from_snapshots(conn):
    # v1.3.x 大 ETF 补齐：历史观测从 etf_snapshots 幂等回填（零外部调用）。
    # 新补品种（无任何观测）取全快照史；已跟踪品种存量观测不被覆盖（DO NOTHING）；
    # metric!='share' 的品种（如 gold 组 holding）不被回填。
    conn.executemany("INSERT INTO etf_snapshots VALUES(?,?,?,?,?,?)", [
        ("2026-10-14", "510500", "500ETF南方", "SSE", 55.0, "t"),
        ("2026-10-15", "510500", "500ETF南方", "SSE", 56.5, "t"),
        ("2026-10-15", "159922", "中证500ETF嘉实", "SZSE", 88.0, "t"),
        ("2026-10-15", "518880", "黄金ETF华安", "SSE", 20.0, "t"),   # gold 组 metric=holding
    ])
    # 已跟踪品种既有观测（日常采集已落库）：值与快照不同，用于验证不被覆盖
    conn.execute(
        "INSERT INTO observations VALUES('159922','share','2026-10-15',100.0,NULL,'daily')")
    conn.commit()
    sql = (Path(__file__).resolve().parents[1] / "migrations"
           / "2026-10-17-etf-category-backfill.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    rows = conn.execute(
        "SELECT trade_date, value FROM observations"
        " WHERE instrument_id='510500' ORDER BY trade_date").fetchall()
    assert [(r["trade_date"], r["value"]) for r in rows] == \
        [("2026-10-14", 55.0), ("2026-10-15", 56.5)]
    # 存量观测保留（值仍 100.0 而非快照 88.0）
    kept = conn.execute(
        "SELECT value FROM observations WHERE instrument_id='159922'").fetchone()
    assert kept["value"] == 100.0
    # holding 品种不回填
    assert conn.execute(
        "SELECT COUNT(*) c FROM observations WHERE instrument_id='518880'"
    ).fetchone()["c"] == 0
    # 幂等：重跑行数不变
    before = conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
    conn.executescript(sql)
    assert conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"] == before


def test_margin_source_seeded(conn):
    # v1.4 融资融券（设计 §5.4）：8 沪深余额类 + 3 全市场杠杆状况类，
    # 新分组「市场资金」驱动侧边栏自动出现；混合单位由 instrument 级 unit 承载
    src = conn.execute(
        "SELECT name, group_name, fetcher, unit, movement_th, lag_days"
        " FROM sources WHERE id='margin'").fetchone()
    assert src["name"] == "融资融券"
    assert src["group_name"] == "市场资金"
    assert src["fetcher"] == "akshare.margin"
    assert src["unit"] == "混合" and src["movement_th"] == 2.0 and src["lag_days"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='margin'"
    ).fetchone()["c"] == 11
    codes = {r["id"]: (r["code"], r["metric"], r["unit"]) for r in conn.execute(
        "SELECT id, code, metric, unit FROM instruments WHERE source_id='margin'")}
    assert codes["margin-sh-financing"] == ("MARGIN-SH-FINANCING", "financing", "亿元")
    assert codes["margin-sz-buying"] == ("MARGIN-SZ-BUYING", "buying", "亿元")
    assert codes["margin-maintenance"] == ("MARGIN-MAINTENANCE", "maintenance", "%")
    assert codes["margin-collateral"] == ("MARGIN-COLLATERAL", "collateral", "亿元")
    assert codes["margin-investors"] == ("MARGIN-INVESTORS", "investors", "万人")
