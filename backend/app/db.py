"""SQLite 连接与 schema 初始化。WAL 模式：并发读不阻塞每日唯一写者。"""
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  group_name TEXT NOT NULL,
  freq TEXT NOT NULL DEFAULT 'daily',
  fetcher TEXT NOT NULL,
  unit TEXT,
  movement_th REAL,
  lag_days INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS instruments (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  code TEXT,
  metric TEXT NOT NULL,
  name TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
  instrument_id TEXT NOT NULL REFERENCES instruments(id),
  metric TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  value REAL,
  raw_json TEXT,
  fetched_at TEXT NOT NULL,
  UNIQUE(instrument_id, metric, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_obs_series
  ON observations(instrument_id, metric, trade_date);
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT,
  trade_date TEXT,
  status TEXT NOT NULL,
  rows INTEGER,
  error TEXT,
  started_at TEXT,
  finished_at TEXT
);
"""


def db_path() -> Path:
    return Path(os.environ.get("DB_PATH", "data/dtp.db"))


def connect(path=None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    seed = Path(__file__).resolve().parent.parent / "seed.sql"
    if seed.exists():
        conn.executescript(seed.read_text(encoding="utf-8"))
    conn.commit()
