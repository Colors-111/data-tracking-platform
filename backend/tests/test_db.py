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
    # 3 原有源 + 4 新 ETF 源（csi500/star50/chinext/dividend-etf）
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 8
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='hs300-etf'"
    ).fetchone()["c"] == 5
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='csi500-etf'"
    ).fetchone()["c"] == 3
    assert conn.execute(
        "SELECT lag_days FROM sources WHERE id='hs300-etf'"
    ).fetchone()["lag_days"] == 1


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
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 8
