import sqlite3

import pytest

from backend.app import db


def test_init_db_creates_schema(conn):
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "instruments", "observations", "job_runs"} <= tables


def test_wal_mode(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_seed_loaded(conn):
    # 8 原有源 - cu-inventory（并入铜组后删除独立源行）+ 4 商品组源（copper/aluminum/alumina/gold）
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 11
    assert conn.execute(
        "SELECT COUNT(*) c FROM sources WHERE id='cu-inventory'"
    ).fetchone()["c"] == 0   # 独立源行删除：执行器不再单独跑它
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='hs300-etf'"
    ).fetchone()["c"] == 5
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='csi500-etf'"
    ).fetchone()["c"] == 3
    # 商品组源抽查：铜 8（含迁入的 cu-inventory）、黄金 8
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='copper'"
    ).fetchone()["c"] == 8
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='gold'"
    ).fetchone()["c"] == 8
    assert conn.execute(
        "SELECT lag_days FROM sources WHERE id='hs300-etf'"
    ).fetchone()["lag_days"] == 1


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
    assert c.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 11
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
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 11
