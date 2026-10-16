# 数据追踪平台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 内网数据追踪平台——每日收盘后自动采集 ETF 份额/大宗商品数据入 SQLite，FastAPI+Vue3+ECharts 看板展示（总览异动榜 / 详情卡片+曲线+对比）。

**Architecture:** systemd timer 触发 `docker compose run --rm collect` 采集（注册表驱动 fetcher、幂等 upsert、校验告警）→ SQLite(WAL) 单文件（宿主机卷）→ FastAPI 只读 API + 托管 Vue 构建产物（唯一常驻容器）。前后端打进同一多阶段镜像，宿主机只需 Docker。

**Tech Stack:** Python 3.12 + uv、FastAPI、akshare≥1.19.1、tenacity、SQLite(WAL)、Vue3 + Vite + vue-echarts(ECharts 6)、Docker/compose、pytest/Vitest。

**Spec:** [docs/PRD.md](../PRD.md) ｜ [设计文档](../specs/2026-10-16-data-tracking-platform-design.md)

**约定（全计划通用）：**
- 仓库根目录 = 所有命令的工作目录
- 后端测试：`uv run pytest`（默认排除真实网络调用；网络冒烟用 `uv run pytest -m network`）
- 红涨绿跌：`#e74c3c` 涨 / `#27ae60` 跌；侧边栏深色 `#1e2430`
- 日期格式统一 `YYYY-MM-DD` 字符串
- 值的单位由 `sources.unit` 定义（亿份/元/吨），`observations.value` 即该单位

---

## M1 · 数据管道

### Task 1: 项目骨架与工具链

**Files:**
- Create: `pyproject.toml`、`.gitignore`（追加）、`backend/__init__.py`、`backend/app/__init__.py`、`backend/fetchers/__init__.py`、`backend/fetchers/scrapers/__init__.py`、`backend/tests/__init__.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "data-tracking-platform"
version = "0.1.0"
description = "内网数据追踪平台：ETF 份额与大宗商品日度数据采集与看板"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "akshare>=1.19.1",
    "tenacity>=9.0",
    "httpx>=0.27",
]

[dependency-groups]
dev = ["pytest>=8.0", "pandas>=2.2"]

[tool.pytest.ini_options]
pythonpath = ["."]
addopts = "-m 'not network'"
markers = ["network: 真实外部接口冒烟，默认跳过，用 -m network 运行"]
```

- [ ] **Step 2: .gitignore 追加（保留已有 `.superpowers/`）**

```
data/
.venv/
__pycache__/
*.pyc
node_modules/
frontend/dist/
.pytest_cache/
```

- [ ] **Step 3: 创建空 `__init__.py`**（上面列出的 6 个文件，内容留空）

- [ ] **Step 4: 安装并锁定依赖**

Run: `uv sync`（自动生成 `uv.lock`）然后 `uv run pytest`
Expected: 依赖安装成功；pytest 输出 "no tests ran"（退出码 5 属正常）

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .gitignore backend/ && git commit -m "chore: 项目骨架（uv + pytest + 目录结构）"
```

---

### Task 2: 数据库层（schema + WAL + 种子数据）

**Files:**
- Create: `backend/app/db.py`、`backend/seed.sql`、`backend/tests/test_db.py`、`backend/tests/conftest.py`

- [ ] **Step 1: 写 backend/seed.sql（首批三源注册表数据）**

```sql
INSERT OR IGNORE INTO sources(id,name,group_name,freq,fetcher,unit,movement_th,lag_days,enabled,sort_order) VALUES
 ('hs300-etf','沪深300 ETF','指数 ETF','daily','akshare.sse_etf_scale','亿份',1.0,1,1,1),
 ('cu-inventory','电解铜库存','大宗商品','daily','akshare.futures_inventory_em','吨',2.0,1,1,2),
 ('coal-price','煤炭价格','大宗商品','daily','scrapers.coal_price','元/吨',1.0,1,1,3);

INSERT OR IGNORE INTO instruments(id,source_id,code,metric,name,sort_order) VALUES
 ('510300','hs300-etf','510300','share','沪深300ETF华泰柏瑞',1),
 ('510310','hs300-etf','510310','share','沪深300ETF易方达',2),
 ('510320','hs300-etf','510320','share','沪深300ETF中金',3),
 ('510330','hs300-etf','510330','share','沪深300ETF华夏',4),
 ('510350','hs300-etf','510350','share','沪深300ETF工银',5),
 ('cu-inventory','cu-inventory','cu','inventory','电解铜库存',1),
 ('coal-price','coal-price',NULL,'price','动力煤现货价',1);
```

- [ ] **Step 2: 写失败测试 backend/tests/conftest.py + test_db.py**

```python
# backend/tests/conftest.py
import pytest
from backend.app import db

@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.init_db(c)
    yield c
    c.close()
```

```python
# backend/tests/test_db.py
def test_init_db_creates_schema(conn):
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "instruments", "observations", "job_runs"} <= tables

def test_wal_mode(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

def test_seed_loaded(conn):
    assert conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 3
    assert conn.execute(
        "SELECT COUNT(*) c FROM instruments WHERE source_id='hs300-etf'"
    ).fetchone()["c"] == 5
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
```

- [ ] **Step 3: 运行确认失败**

Run: `uv run pytest backend/tests/test_db.py -v`
Expected: FAIL（`ModuleNotFoundError ... 'backend.app.db'`）

- [ ] **Step 4: 写 backend/app/db.py**

```python
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
```

- [ ] **Step 5: 运行确认通过**

Run: `uv run pytest backend/tests/test_db.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/seed.sql backend/tests/ && git commit -m "feat: SQLite schema/WAL/种子数据（注册表三源）"
```

---

### Task 3: Fetcher 协议与注册表

**Files:**
- Create: `backend/fetchers/base.py`、`backend/tests/test_base.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_base.py
import pytest
from backend.fetchers.base import Observation, Fetcher, register, build_fetcher

def test_observation_fields():
    o = Observation("510300", "share", "2026-10-15", 254.85, {"k": "v"})
    assert o.instrument_id == "510300" and o.metric == "share"
    assert o.trade_date == "2026-10-15" and o.value == 254.85

def test_register_and_build():
    @register("fake.demo")
    class DemoFetcher(Fetcher):
        lag_days = 0
        def fetch(self, trade_date):
            return []

    src = {"id": "demo"}
    f = build_fetcher("fake.demo", src, [])
    assert isinstance(f, DemoFetcher)
    with pytest.raises(KeyError):
        build_fetcher("nope.missing", src, [])
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_base.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 backend/fetchers/base.py**

```python
"""Fetcher 协议与注册表。新增数据源 = 实现 Fetcher + @register + sources 配置行。"""
from dataclasses import dataclass
from typing import Protocol

@dataclass
class Observation:
    instrument_id: str
    metric: str
    trade_date: str          # 'YYYY-MM-DD'
    value: float | None
    raw: dict | None = None

class Fetcher(Protocol):
    source_id: str
    lag_days: int            # 数据发布滞后天数（T+1 源=1）
    def fetch(self, trade_date: str) -> list[Observation]: ...

_REGISTRY: dict[str, type] = {}

def register(name: str):
    def deco(cls):
        _REGISTRY[name] = cls
        if not getattr(cls, "source_id", None):
            cls.source_id = name.split(".")[-1]
        return cls
    return deco

def build_fetcher(name: str, source, instruments: list) -> Fetcher:
    if name not in _REGISTRY:
        raise KeyError(f"未注册的 fetcher: {name}")
    return _REGISTRY[name](source, instruments)
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_base.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/fetchers/base.py backend/tests/test_base.py && git commit -m "feat: Fetcher 协议与注册表"
```

---

### Task 4: 采集真实接口样本做 fixture（网络，一次性）

**Files:**
- Create: `backend/tests/gen_fixtures.py`、`backend/tests/fixtures/*.json`

- [ ] **Step 1: 写采样脚本**

```python
# backend/tests/gen_fixtures.py
"""一次性运行：uv run pytest -m network 不适用，直接 uv run python -m backend.tests.gen_fixtures
   用真实接口录制样本，供 fetcher 离线单测使用。"""
import json
from pathlib import Path
import akshare as ak

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)
DAY = "20260930"  # 已收盘的历史交易日

def dump(name, df):
    (OUT / name).write_text(
        df.to_json(orient="records", force_ascii=False), encoding="utf-8")
    print(name, len(df), "行, 列:", list(df.columns))

dump("sse_etf_scale.json", ak.fund_etf_scale_sse(DAY))
dump("futures_inventory_cu.json", ak.futures_inventory_em(symbol="cu"))
```

- [ ] **Step 2: 运行采样（需网络）**

Run: `uv run python -m backend.tests.gen_fixtures`
Expected: 打印两个文件的行数与**真实列名**（后续 fetcher 按此调整列名候选）；`backend/tests/fixtures/` 出现两个 json

- [ ] **Step 3: 提交 fixture（若接口失败：记录报错并跳过该源，fetcher 任务改用手工构造样本，勿阻塞）**

```bash
git add backend/tests/gen_fixtures.py backend/tests/fixtures/ && git commit -m "test: 真实接口样本 fixture（上交所ETF份额/东财铜库存）"
```

---

### Task 5: 上交所 ETF 份额 fetcher

**Files:**
- Create: `backend/fetchers/akshare_sse_etf.py`、`backend/tests/test_fetcher_sse_etf.py`

- [ ] **Step 1: 写失败测试（用 fixture + mock akshare）**

```python
# backend/tests/test_fetcher_sse_etf.py
import json
from pathlib import Path
import pandas as pd
import pytest
from backend.app import db
from backend.fetchers.base import build_fetcher

FIX = Path(__file__).parent / "fixtures" / "sse_etf_scale.json"

@pytest.fixture
def sse_env(conn, monkeypatch):
    src = conn.execute("SELECT * FROM sources WHERE id='hs300-etf'").fetchone()
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id='hs300-etf' ORDER BY sort_order"
    ).fetchall()
    df = pd.read_json(FIX) if FIX.exists() else pd.DataFrame([
        # 手工兜底样本（列名以 Task 4 实测为准，二选一候选）
        {"基金代码": "510300", "基金简称": "沪深300ETF", "基金份额(亿份)": 254.85},
        {"基金代码": "510310", "基金简称": "沪深300ETF易方达", "基金份额(亿份)": 167.26},
    ])
    import backend.fetchers.akshare_sse_etf as mod
    monkeypatch.setattr(mod.ak, "fund_etf_scale_sse", lambda d: df)
    return build_fetcher(src["fetcher"], src, insts)

def test_fetch_filters_target_codes(sse_env):
    obs = sse_env.fetch("2026-09-30")
    ids = {o.instrument_id for o in obs}
    assert {"510300", "510310"} <= ids and len(ids) == 5
    assert all(o.metric == "share" and o.trade_date == "2026-09-30" for o in obs)

def test_fetch_values_are_numbers(sse_env):
    obs = sse_env.fetch("2026-09-30")
    by_id = {o.instrument_id: o for o in obs}
    assert by_id["510300"].value is not None and by_id["510300"].value > 0

def test_lag_days_is_one(sse_env):
    assert sse_env.lag_days == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_fetcher_sse_etf.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 backend/fetchers/akshare_sse_etf.py**

```python
"""上交所 ETF 份额：fund_etf_scale_sse(date) 返回当日全所 ETF 快照，历史可回溯 2015。
   T+1 公布 → lag_days=1。列名以 fixtures/sse_etf_scale.json 实测为准（多候选兼容）。"""
import akshare as ak
from .base import Observation, register

CODE_CANDIDATES = ["基金代码", "证券代码", "代码"]
SHARE_CANDIDATES = ["基金份额(亿份)", "基金份额", "份额合计(亿份)", "份额"]

def _col(df, candidates, what):
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(f"未找到{what}列，实际列: {list(df.columns)}")

@register("akshare.sse_etf_scale")
class SseEtfScaleFetcher:
    source_id = "hs300-etf"
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        # code -> instrument_id（只跟踪注册的成分基金）
        self.targets = {i["code"]: i["id"] for i in instruments if i["code"]}

    def fetch(self, trade_date: str) -> list[Observation]:
        df = ak.fund_etf_scale_sse(trade_date.replace("-", ""))
        if df is None or df.empty:
            return []
        c_code = _col(df, CODE_CANDIDATES, "代码")
        c_share = _col(df, SHARE_CANDIDATES, "份额")
        out = []
        for _, r in df.iterrows():
            code = str(r[c_code]).zfill(6)
            if code in self.targets:
                out.append(Observation(
                    self.targets[code], "share", trade_date,
                    float(r[c_share]), {"name": str(r.get("基金简称", ""))}))
        return out
```

注意：`backend/fetchers/__init__.py` 需 import 两个 fetcher 模块以触发注册（Task 6 完成后统一加）：

```python
# backend/fetchers/__init__.py（本任务先只加一行）
from . import akshare_sse_etf  # noqa: F401
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_fetcher_sse_etf.py -v`
Expected: 3 passed（若列名候选与 fixture 不符：按 Task 4 打印的真实列名更新 `*_CANDIDATES` 后重跑）

- [ ] **Step 5: Commit**

```bash
git add backend/fetchers/ backend/tests/test_fetcher_sse_etf.py && git commit -m "feat: 上交所ETF份额 fetcher（T+1，注册目标基金过滤）"
```

---

### Task 6: 电解铜库存 fetcher

**Files:**
- Create: `backend/fetchers/akshare_cu_inventory.py`、`backend/tests/test_fetcher_cu.py`
- Modify: `backend/fetchers/__init__.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_fetcher_cu.py
import json
from pathlib import Path
import pandas as pd
import pytest
from backend.fetchers.base import build_fetcher

FIX = Path(__file__).parent / "fixtures" / "futures_inventory_cu.json"

@pytest.fixture
def cu_fetcher(conn):
    src = conn.execute("SELECT * FROM sources WHERE id='cu-inventory'").fetchone()
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id='cu-inventory'").fetchall()
    df = pd.read_json(FIX) if FIX.exists() else pd.DataFrame([
        {"日期": "2026-09-30", "库存": 102000.0},
        {"日期": "2026-10-09", "库存": 101500.0},
    ])
    import backend.fetchers.akshare_cu_inventory as mod
    # monkeypatch 类属性级 ak 引用
    mod.ak = __import__("types").SimpleNamespace(futures_inventory_em=lambda s: df)
    return build_fetcher(src["fetcher"], src, insts)

def test_returns_full_history(cu_fetcher):
    obs = cu_fetcher.fetch("2026-10-16")   # 传任意日期，接口本身返回近60日全量
    assert len(obs) >= 2
    assert all(o.metric == "inventory" for o in obs)
    assert all(o.instrument_id == "cu-inventory" for o in obs)

def test_dates_normalized(cu_fetcher):
    obs = cu_fetcher.fetch("2026-10-16")
    assert all(len(o.trade_date) == 10 and o.trade_date[4] == "-" for o in obs)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_fetcher_cu.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 backend/fetchers/akshare_cu_inventory.py**

```python
"""电解铜库存：futures_inventory_em(symbol='cu') 东财口径，返回近约 60 个交易日全量。
   关键约束：每次抓取把整个返回区间全部 upsert（接口只保留 60 日，历史须自建累积）。"""
import akshare as ak
from .base import Observation, register

DATE_CANDIDATES = ["日期", "date", "报告日期"]
VAL_CANDIDATES = ["库存", "库存量", "inventory", "电解铜库存"]

def _col(df, candidates, what):
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(f"未找到{what}列，实际列: {list(df.columns)}")

@register("akshare.futures_inventory_em")
class FuturesInventoryFetcher:
    source_id = "cu-inventory"
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        # instrument.code -> (instrument_id, em_symbol)
        self.targets = {i["id"]: i["code"] for i in instruments if i["code"]}

    def fetch(self, trade_date: str) -> list[Observation]:
        out = []
        for iid, symbol in self.targets.items():
            df = ak.futures_inventory_em(symbol=symbol)
            if df is None or df.empty:
                continue
            c_d, c_v = _col(df, DATE_CANDIDATES, "日期"), _col(df, VAL_CANDIDATES, "库存")
            for _, r in df.iterrows():
                d = str(r[c_d])[:10].replace("/", "-")
                v = r[c_v]
                if v is None or pd_isna(v):
                    continue
                out.append(Observation(iid, "inventory", d, float(v)))
        return out

def pd_isna(v):
    return v != v  # NaN 判定，避免强依赖 pandas
```

- [ ] **Step 4: 注册进包**

```python
# backend/fetchers/__init__.py（完整内容）
from . import akshare_sse_etf, akshare_cu_inventory  # noqa: F401
```

- [ ] **Step 5: 运行确认通过**

Run: `uv run pytest backend/tests/test_fetcher_cu.py -v`
Expected: 2 passed（列名按 fixture 实测调整候选）

- [ ] **Step 6: Commit**

```bash
git add backend/fetchers/ backend/tests/test_fetcher_cu.py && git commit -m "feat: 电解铜库存 fetcher（全量区间 upsert 自建历史）"
```

---

### Task 7: 采集执行器（逐源执行 + 幂等 upsert）

**Files:**
- Create: `backend/collect.py`、`backend/tests/test_collect.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_collect.py
import pytest
from backend.app import db
from backend.fetchers.base import Observation, register
from backend import collect as C

@pytest.fixture
def fake_source(conn):
    conn.execute("INSERT OR REPLACE INTO sources VALUES"
                 "('fake','假源','指数 ETF','daily','fake.ok','x',1.0,0,1,99)")
    conn.execute("INSERT OR REPLACE INTO instruments VALUES"
                 "('f1','fake','F1','price','假品种',1)")
    conn.commit()

@register("fake.ok")
class OkFetcher:
    source_id = "fake"
    lag_days = 0
    def __init__(self, source, instruments): pass
    def fetch(self, trade_date):
        return [Observation("f1", "price", trade_date, 42.0)]

@register("fake.boom")
class BoomFetcher:
    source_id = "fake"
    lag_days = 0
    def __init__(self, source, instruments): pass
    def fetch(self, trade_date):
        raise RuntimeError("接口爆炸")

def _run(conn, src_id="fake", **kw):
    src = conn.execute("SELECT * FROM sources WHERE id=?", (src_id,)).fetchone()
    insts = conn.execute("SELECT * FROM instruments WHERE source_id=?", (src_id,)).fetchall()
    return C.run_source(conn, src, insts, "2026-10-16", **kw)

def test_run_source_ok_and_idempotent(conn, fake_source):
    r1 = _run(conn)
    assert r1["status"] == "ok" and r1["rows"] == 1
    n1 = conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
    _run(conn)  # 重跑
    n2 = conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
    assert n1 == n2 == 1          # 幂等
    assert conn.execute("SELECT COUNT(*) c FROM job_runs").fetchone()["c"] == 2

def test_run_source_failure_isolated(conn, fake_source):
    conn.execute("UPDATE sources SET fetcher='fake.boom' WHERE id='fake'"); conn.commit()
    r = _run(conn)
    assert r["status"] == "failed" and "接口爆炸" in r["error"]
    assert conn.execute(
        "SELECT status FROM job_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()["status"] == "failed"

def test_run_source_empty(conn, fake_source):
    @register("fake.empty")
    class E:
        source_id = "fake"; lag_days = 0
        def __init__(self, source, instruments): pass
        def fetch(self, trade_date): return []
    conn.execute("UPDATE sources SET fetcher='fake.empty' WHERE id='fake'"); conn.commit()
    assert _run(conn)["status"] == "empty"

def test_target_date_respects_lag(conn, fake_source, monkeypatch):
    seen = {}
    @register("fake.lag")
    class L:
        source_id = "fake"; lag_days = 1
        def __init__(self, source, instruments): pass
        def fetch(self, trade_date):
            seen["d"] = trade_date
            return [Observation("f1", "price", trade_date, 1.0)]
    conn.execute("UPDATE sources SET fetcher='fake.lag' WHERE id='fake'"); conn.commit()
    _run(conn)
    assert seen["d"] == "2026-10-15"   # lag_days=1 → 采集目标日=交易日-1
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_collect.py -v`
Expected: FAIL（`backend.collect` 不存在）

- [ ] **Step 3: 写 backend/collect.py**

```python
"""采集入口：uv run python -m backend.collect [--backfill --from D --to D --source ID]"""
import argparse
import datetime as dt
import json
import logging
import sys
from tenacity import retry, stop_after_attempt, wait_exponential

from .app.db import connect, init_db
from .fetchers.base import build_fetcher
from . import fetchers  # noqa: F401 触发注册
from .notify import send_summary

log = logging.getLogger("collect")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
def fetch_with_retry(fetcher, trade_date):
    return fetcher.fetch(trade_date)

def upsert_observations(conn, obs, now_iso):
    conn.executemany(
        """INSERT INTO observations
             (instrument_id, metric, trade_date, value, raw_json, fetched_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(instrument_id, metric, trade_date) DO UPDATE SET
             value=excluded.value, raw_json=excluded.raw_json, fetched_at=excluded.fetched_at""",
        [(o.instrument_id, o.metric, o.trade_date, o.value,
          json.dumps(o.raw, ensure_ascii=False) if o.raw else None, now_iso)
         for o in obs])
    conn.commit()

def source_max_date(conn, source_id):
    r = conn.execute(
        """SELECT MAX(o.trade_date) m FROM observations o
           JOIN instruments i ON i.id=o.instrument_id WHERE i.source_id=?""",
        (source_id,)).fetchone()
    return r["m"]

def run_source(conn, source, instruments, run_date: str) -> dict:
    started = dt.datetime.now().isoformat(timespec="seconds")
    result = {"source_id": source["id"], "rows": 0, "status": "ok", "error": None}
    try:
        fetcher = build_fetcher(source["fetcher"], source, instruments)
        target = (dt.date.fromisoformat(run_date)
                  - dt.timedelta(days=fetcher.lag_days)).isoformat()
        obs = fetch_with_retry(fetcher, target)
        upsert_observations(conn, obs, started)
        result["rows"] = len(obs)
        result["target_date"] = target
        if not obs:
            result["status"] = "empty"
        elif (m := source_max_date(conn, source["id"])) and m < target:
            result["status"] = "stale"
    except Exception as e:  # 单源失败不阻断其他源
        log.exception("source %s failed", source["id"])
        result["status"], result["error"] = "failed", repr(e)
    conn.execute(
        """INSERT INTO job_runs(source_id,trade_date,status,rows,error,started_at,finished_at)
           VALUES (?,?,?,?,?,?,?)""",
        (source["id"], run_date, result["status"], result["rows"],
         result["error"], started, dt.datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return result

def trade_dates_from_to(d_from: str, d_to: str) -> list[str]:
    """交易日历（akshare）；失败降级为工作日，并发 warning。"""
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        days = {str(d) for d in df["trade_date"].astype(str)}
        out, cur = [], dt.date.fromisoformat(d_from)
        while cur <= dt.date.fromisoformat(d_to):
            if cur.isoformat() in days or cur.strftime("%Y%m%d") in days:
                out.append(cur.isoformat())
            cur += dt.timedelta(days=1)
        return out
    except Exception:
        log.warning("交易日历获取失败，降级为工作日")
        out, cur = [], dt.date.fromisoformat(d_from)
        while cur <= dt.date.fromisoformat(d_to):
            if cur.weekday() < 5:
                out.append(cur.isoformat())
            cur += dt.timedelta(days=1)
        return out

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--from", dest="d_from")
    ap.add_argument("--to", dest="d_to", default=dt.date.today().isoformat())
    ap.add_argument("--source", help="只跑指定 source id（回补/调试用）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    conn = connect()
    init_db(conn)
    where, params = ("WHERE s.enabled=1", ()) if not args.source else \
                    ("WHERE s.id=?", (args.source,))
    sources = conn.execute(f"SELECT * FROM sources {where} ORDER BY sort_order",
                           params).fetchall()

    if args.backfill:
        dates = trade_dates_from_to(args.d_from, args.d_to)
        log.info("回补 %s ~ %s，共 %d 个交易日", args.d_from, args.d_to, len(dates))
        for s in sources:
            have = source_max_date(conn, s["id"])
            todo = [d for d in dates if not have or d > have]  # 断点续跑
            for d in todo:
                insts = conn.execute(
                    "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
                    (s["id"],)).fetchall()
                run_source(conn, s, insts, d)
    else:
        results = []
        for s in sources:
            insts = conn.execute(
                "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
                (s["id"],)).fetchall()
            results.append(run_source(conn, s, insts, args.date))
        send_summary(results)
        bad = [r for r in results if r["status"] != "ok"]
        log.info("完成：%d 源，%d 异常", len(results), len(bad))
        conn.close()
        return 1 if bad else 0

    conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_collect.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/collect.py backend/tests/test_collect.py && git commit -m "feat: 采集执行器（逐源隔离/重试/幂等upsert/lag目标日）"
```

---

### Task 8: stale 校验与 webhook 汇总通知

**Files:**
- Create: `backend/notify.py`、`backend/tests/test_notify.py`、`backend/tests/test_validation.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_notify.py
import pytest
from backend import notify

def test_renders_summary_with_status():
    results = [
        {"source_id": "a", "rows": 5, "status": "ok", "error": None},
        {"source_id": "b", "rows": 0, "status": "failed", "error": "RuntimeError('x')"},
    ]
    text = notify.render_summary(results)
    assert "a" in text and "ok" in text and "failed" in text and "x" in text

def test_send_skips_without_url(monkeypatch):
    called = []
    monkeypatch.setattr(notify.httpx, "post",
                        lambda *a, **k: called.append((a, k)))
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    notify.send_summary([{"source_id": "a", "rows": 1, "status": "ok", "error": None}])
    assert called == []

def test_send_posts_wechat_format(monkeypatch):
    called = {}
    def fake_post(url, json=None, timeout=None):
        called.update(url=url, payload=json)
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify.send_summary([{"source_id": "a", "rows": 1, "status": "ok", "error": None}])
    assert called["url"] == "https://example.com/hook"
    assert called["payload"]["msgtype"] == "text"
```

```python
# backend/tests/test_validation.py
"""stale 判定：库内最新日期落后于采集目标日（考虑 lag_days 已折算）。"""
import sqlite3
from backend.app import db

def _seed_pair(conn):
    conn.execute("INSERT OR REPLACE INTO sources VALUES"
                 "('v','V','g','daily','fake.ok','x',1.0,0,1,9)")
    conn.execute("INSERT OR REPLACE INTO instruments VALUES"
                 "('v1','v','V1','price','v',1)")
    conn.execute("INSERT INTO observations VALUES"
                 "('v1','price','2026-10-10',1.0,NULL,'t')")
    conn.commit()

def test_stale_when_behind_target(conn):
    from backend.fetchers.base import Observation, register
    @register("fake.ok")
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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_notify.py backend/tests/test_validation.py -v`
Expected: FAIL（notify 不存在）

- [ ] **Step 3: 写 backend/notify.py**

```python
"""采集汇总推送：企业微信/钉钉 text 消息格式（二者 msgtype 兼容）。无 WEBHOOK_URL 时静默跳过。"""
import os
import httpx

STATUS_CN = {"ok": "✅", "empty": "⚠️空", "stale": "⚠️陈旧", "failed": "❌失败"}

def render_summary(results: list[dict]) -> str:
    lines = ["【数据追踪平台·采集汇总】"]
    for r in results:
        line = f"{STATUS_CN.get(r['status'], r['status'])} {r['source_id']}: {r['rows']} 行"
        if r.get("error"):
            line += f" — {r['error']}"
        lines.append(line)
    return "\n".join(lines)

def send_summary(results: list[dict]) -> None:
    url = os.environ.get("WEBHOOK_URL")
    if not url or not results:
        return
    try:
        httpx.post(url, json={"msgtype": "text",
                              "text": {"content": render_summary(results)}},
                   timeout=10)
    except Exception:
        pass  # 通知失败不影响采集结果，job_runs 已留痕
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_notify.py backend/tests/test_validation.py -v`
Expected: 4 passed（stale 测试依赖 Task 7 的 `(m := ...)` 校验逻辑，若失败检查该行）

- [ ] **Step 5: Commit**

```bash
git add backend/notify.py backend/tests/test_notify.py backend/tests/test_validation.py && git commit -m "feat: webhook 汇总通知 + stale 静默失败校验"
```

---

### Task 9: Docker 化（多阶段构建 + compose）

**Files:**
- Create: `Dockerfile`、`docker-compose.yml`、`.dockerignore`

- [ ] **Step 1: 写 Dockerfile（两版 fe 阶段，按阶段选用）**

**最终版**（Task 13 完成、有 `package-lock.json` 后替换使用）：

```dockerfile
# ---- 阶段1：前端构建（Node 环境隔离在镜像内）----
FROM node:20-slim AS fe
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 阶段2：Python 运行时（含 dev 组，容器内可跑 pytest）----
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY backend/ backend/
COPY --from=fe /fe/dist/ frontend/dist/
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**M1 当前占位版**（frontend 尚无 lockfile，npm ci 会失败——Task 13 后换最终版）：

```dockerfile
FROM node:20-slim AS fe
WORKDIR /fe
COPY frontend/ ./
RUN echo "frontend placeholder" > dist/index.html

# 阶段2 与最终版完全相同，仅把
#   COPY --from=fe /fe/dist/ frontend/dist/
# 保持不变即可
```

- [ ] **Step 2: 写 frontend 占位目录（保证 COPY 可用；Task 13 替换为真实工程）**

```bash
mkdir -p frontend && echo "placeholder" > frontend/index.html
```

- [ ] **Step 3: 写 docker-compose.yml**

```yaml
services:
  web:
    build: .
    ports: ["8000:8000"]
    volumes: ["./data:/app/data"]
    environment:
      - TZ=Asia/Shanghai
      - DB_PATH=/app/data/dtp.db
    restart: unless-stopped

  collect:
    build: .
    volumes: ["./data:/app/data"]
    environment:
      - TZ=Asia/Shanghai
      - DB_PATH=/app/data/dtp.db
      - WEBHOOK_URL=${WEBHOOK_URL:-}
    command: ["uv", "run", "--no-sync", "python", "-m", "backend.collect"]
    profiles: ["tools"]   # 不随 up 启动，仅 docker compose run --rm collect 调用
```

- [ ] **Step 4: 写 .dockerignore**

```
.git
.venv
data
node_modules
frontend/dist
.superpowers
.pytest_cache
__pycache__
docs
```

- [ ] **Step 5: 构建并验证容器内跑通采集单测**

Run: `docker compose build` 期望成功；`docker compose run --rm collect uv run --no-sync python -m pytest -q` 期望全部通过（容器内环境隔离验证）

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore frontend/ && git commit -m "build: Docker 多阶段镜像 + compose（web 常驻/collect 按需）"
```

---

### Task 10: systemd 部署单元与 README

**Files:**
- Create: `deploy/systemd/data-collect.timer`、`deploy/systemd/data-collect.service`、`deploy/systemd/data-notify@.service`
- Modify: `README.md`

- [ ] **Step 1: 写三个 unit 文件**

```ini
# deploy/systemd/data-collect.timer
[Unit]
Description=数据追踪平台·每日采集定时器

[Timer]
OnCalendar=Mon..Fri *-*-* 16:40:00
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# deploy/systemd/data-collect.service
[Unit]
Description=数据追踪平台·采集任务（docker compose run）

[Service]
Type=oneshot
WorkingDirectory=/opt/dtp
ExecStart=docker compose run --rm collect
OnFailure=data-notify@%n
```

```ini
# deploy/systemd/data-notify@.service
[Unit]
Description=采集进程崩溃告警 %i

[Service]
Type=oneshot
EnvironmentFile=-/opt/dtp/deploy/notify.env
ExecStart=/usr/bin/curl -s -X POST ${WEBHOOK_URL} \
  -H "Content-Type: application/json" \
  -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"数据平台采集进程崩溃: %i\"}}"
```

- [ ] **Step 2: README 增加部署一节**

````markdown
## 快速开始（内网部署）

```bash
# 构建 + 启动 Web（http://<内网IP>:8000）
docker compose up -d --build web

# 首次回补历史（沪深300 份额自 2026-01-01，可改 --from 2015-01-01 全量）
docker compose run --rm collect uv run --no-sync python -m backend.collect \
  --backfill --from 2026-01-01

# 定时采集（宿主机）
sudo cp deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now data-collect.timer
```
````

- [ ] **Step 3: Commit**

```bash
git add deploy/ README.md && git commit -m "deploy: systemd timer/service/崩溃告警单元 + README 快速开始"
```

---

## M2 · 看板核心

### Task 11: FastAPI 应用 + /api/sources + 详情端点

**Files:**
- Create: `backend/app/main.py`、`backend/app/schemas.py`、`backend/app/api/__init__.py`、`backend/app/api/sources.py`
- Test: `backend/tests/test_api_sources.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_api_sources.py
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

@pytest.fixture
def client(conn):
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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_api_sources.py -v`
Expected: FAIL（main 不存在）

- [ ] **Step 3: 写 db.py 依赖与 main.py、sources.py**

`backend/app/db.py` 追加（文件末尾）：

```python
def get_conn():
    """FastAPI 依赖：每请求一个连接（读密集小库，无池也够用）。"""
    c = connect()
    try:
        yield c
    finally:
        c.close()
```

```python
# backend/app/main.py
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .db import init_db, connect
from .api import sources, series, movements, jobs

app = FastAPI(title="数据追踪平台")
for r in (sources.router, series.router, movements.router, jobs.router):
    app.include_router(r)

FE_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"

@app.on_event("startup")
def _startup():
    conn = connect()
    init_db(conn)      # 保证新库自动建表+种子
    conn.close()

if FE_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FE_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = FE_DIR / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(FE_DIR / "index.html")   # SPA fallback
```

（series/movements/jobs 路由在 Task 12/16 创建；为让本任务可运行，先建空路由文件——见 Step 4）

```python
# backend/app/schemas.py（本任务用到的心智模型，非必需字段校验从简）
# API 返回直接用 dict；schema 仅作文档：SourceGroup / SourceCard / SourceDetail
```

```python
# backend/app/api/sources.py
from fastapi import APIRouter, Depends, HTTPException, Query
from ..db import get_conn

router = APIRouter()
RANGE_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "all": None}

@router.get("/api/sources")
def list_sources(conn=Depends(get_conn)):
    rows = conn.execute(
        """SELECT s.id, s.name, s.group_name, s.unit, s.sort_order,
                  (SELECT COUNT(*) FROM instruments i WHERE i.source_id=s.id) AS inst_count,
                  (SELECT MAX(o.trade_date) FROM observations o
                     JOIN instruments i2 ON i2.id=o.instrument_id
                    WHERE i2.source_id=s.id) AS latest_date
             FROM sources s WHERE s.enabled=1 ORDER BY s.sort_order""").fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["group_name"], []).append(dict(r))
    return [{"group": g, "sources": ss} for g, ss in groups.items()]

@router.get("/api/sources/{sid}")
def source_detail(sid: str, range: str = Query("3m"), conn=Depends(get_conn)):
    src = conn.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not src:
        raise HTTPException(404, "source not found")
    days = RANGE_DAYS.get(range)
    if days is None and range != "all":
        raise HTTPException(400, "bad range")
    as_of = conn.execute(
        """SELECT MAX(o.trade_date) m FROM observations o
             JOIN instruments i ON i.id=o.instrument_id WHERE i.source_id=?""",
        (sid,)).fetchone()["m"]
    if not as_of:
        return {"id": sid, "name": src["name"], "unit": src["unit"],
                "as_of_date": None, "cards": [], "series": {}}
    day_filter = f"AND o.trade_date >= date('{as_of}', '-{days} days')" if days else ""
    rows = conn.execute(
        f"""SELECT o.instrument_id, o.trade_date, o.value,
                   FIRST_VALUE(o.value) OVER w AS first_v,
                   LAG(o.value)       OVER w AS prev_v,
                   ROW_NUMBER() OVER w2      AS rn_desc
              FROM observations o
              JOIN instruments i ON i.id=o.instrument_id
             WHERE i.source_id=? {day_filter}
             WINDOW w AS (PARTITION BY o.instrument_id ORDER BY o.trade_date),
                    w2 AS (PARTITION BY o.instrument_id ORDER BY o.trade_date DESC)""",
        (sid,)).fetchall()
    cards, series = [], {}
    by_inst: dict[str, list] = {}
    for r in rows:
        by_inst.setdefault(r["instrument_id"], []).append(r)
    for inst in conn.execute(
            "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
            (sid,)).fetchall():
        rs = by_inst.get(inst["id"], [])
        pts = [(r["trade_date"], r["value"]) for r in reversed(rs)]
        series[inst["id"]] = pts
        if rs:
            last = next(r for r in rs if r["rn_desc"] == 1)
            card = {"instrument_id": inst["id"], "name": inst["name"],
                    "code": inst["code"], "value": last["value"],
                    "day_change": None, "day_pct": None,
                    "range_change": None, "range_pct": None}
            if last["prev_v"] is not None and last["prev_v"] != 0:
                card["day_change"] = round(last["value"] - last["prev_v"], 4)
                card["day_pct"] = round((last["value"] / last["prev_v"] - 1) * 100, 2)
            if last["first_v"] not in (None, 0) and last["first_v"] != last["value"]:
                card["range_change"] = round(last["value"] - last["first_v"], 4)
                card["range_pct"] = round((last["value"] / last["first_v"] - 1) * 100, 2)
            cards.append(card)
    return {"id": sid, "name": src["name"], "unit": src["unit"],
            "as_of_date": as_of, "cards": cards, "series": series}
```

```python
# backend/app/api/series.py（Task 12 实装，先占位通过导入）
from fastapi import APIRouter, Depends, Query
from ..db import get_conn
router = APIRouter()

@router.get("/api/series")
def series(instruments: str = Query(""), normalize: int = 0,
           range: str = Query("6m"), conn=Depends(get_conn)):
    return {"as_of_date": None, "series": []}   # Task 12 实装
```

`backend/app/api/movements.py`、`backend/app/api/jobs.py` 同样先建占位空路由（Task 12 实装）：

```python
# backend/app/api/movements.py（占位，Task 12 实装）
from fastapi import APIRouter, Depends, Query
from ..db import get_conn

router = APIRouter()

@router.get("/api/movements")
def movements(date: str = Query(None), conn=Depends(get_conn)):
    return {"as_of_date": None, "movements": []}
```

```python
# backend/app/api/jobs.py（占位，Task 12 实装）
from fastapi import APIRouter, Depends
from ..db import get_conn

router = APIRouter()

@router.get("/api/jobs/latest")
def jobs_latest(conn=Depends(get_conn)):
    return {"jobs": []}
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_api_sources.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ backend/tests/test_api_sources.py && git commit -m "feat: FastAPI + /api/sources + 详情端点（窗口函数算变化）"
```

---

### Task 12: /api/movements + /api/jobs/latest

**Files:**
- Modify: `backend/app/api/movements.py`、`backend/app/api/jobs.py`
- Test: `backend/tests/test_api_movements.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_api_movements.py
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

@pytest.fixture
def client(conn):
    import backend.app.db as dbm
    app.dependency_overrides[dbm.get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def seeded(conn):
    # 510300: 100 -> 103 (+3% ≥ 阈值1%)；510310: 50 -> 50.2 (+0.4% < 阈值)
    for iid, d, v in [("510300","2026-10-14",100),("510300","2026-10-15",103),
                      ("510310","2026-10-14",50),("510310","2026-10-15",50.2),
                      ("510330","2026-10-15",86.2)]:
        conn.execute("INSERT INTO observations VALUES(?,?,?,NULL,'t')", (iid, "share", d, v))
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
    for iid, d, v in [("510300","2026-10-14",100),("510300","2026-10-15",110),
                      ("510310","2026-10-14",50),("510310","2026-10-15",50.6)]:
        conn.execute("INSERT INTO observations VALUES(?,?,?,NULL,'t')", (iid, "share", d, v))
    conn.commit()
    items = client.get("/api/movements").json()["movements"]
    assert [m["instrument_id"] for m in items] == ["510300", "510310"]

def test_jobs_latest(client, conn):
    conn.execute("INSERT INTO job_runs(source_id,trade_date,status,rows,error,started_at,finished_at)"
                 " VALUES('hs300-etf','2026-10-15','ok',5,NULL,'t','t')")
    conn.execute("INSERT INTO job_runs(source_id,trade_date,status,rows,error,started_at,finished_at)"
                 " VALUES('cu-inventory','2026-10-15','failed',0,'RuntimeError()','t','t')")
    conn.commit()
    body = client.get("/api/jobs/latest").json()
    statuses = {j["source_id"]: j["status"] for j in body["jobs"]}
    assert statuses == {"hs300-etf": "ok", "cu-inventory": "failed"}
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_api_movements.py -v`
Expected: FAIL（movements 返回空占位）

- [ ] **Step 3: 实装 movements.py 与 jobs.py**

```python
# backend/app/api/movements.py
from fastapi import APIRouter, Depends, Query
from ..db import get_conn

router = APIRouter()

@router.get("/api/movements")
def movements(date: str = Query(None), conn=Depends(get_conn)):
    as_of = date or conn.execute("SELECT MAX(trade_date) m FROM observations").fetchone()["m"]
    if not as_of:
        return {"as_of_date": None, "movements": []}
    rows = conn.execute(
        """WITH ranked AS (
             SELECT o.instrument_id, o.trade_date, o.value,
                    LAG(o.value) OVER w AS prev_v,
                    ROW_NUMBER() OVER w2 AS rn
               FROM observations o
              WHERE o.trade_date <= :as_of
             WINDOW w  AS (PARTITION BY o.instrument_id, o.metric ORDER BY o.trade_date),
                    w2 AS (PARTITION BY o.instrument_id, o.metric ORDER BY o.trade_date DESC)
           ), spark AS (
             SELECT instrument_id, trade_date, value,
                    ROW_NUMBER() OVER (PARTITION BY instrument_id ORDER BY trade_date DESC) rn
               FROM observations WHERE trade_date <= :as_of
           )
           SELECT r.instrument_id, r.trade_date, r.value, r.prev_v,
                  i.name AS instrument_name, s.id AS source_id, s.name AS source_name, s.unit,
                  (SELECT group_concat(v) FROM (
                     SELECT CAST(value AS TEXT) v FROM spark
                      WHERE instrument_id=r.instrument_id AND rn<=5 ORDER BY trade_date)) AS spark5
             FROM ranked r
             JOIN instruments i ON i.id=r.instrument_id
             JOIN sources s ON s.id=i.source_id
            WHERE r.rn=1 AND s.enabled=1
              AND r.prev_v IS NOT NULL AND r.prev_v != 0
              AND ABS(r.value/r.prev_v - 1)*100 >= COALESCE(s.movement_th, 1e9)
            ORDER BY ABS(r.value/r.prev_v - 1) DESC""",
        {"as_of": as_of}).fetchall()
    movements = []
    for r in rows:
        pct = (r["value"] / r["prev_v"] - 1) * 100
        movements.append({
            "instrument_id": r["instrument_id"], "instrument_name": r["instrument_name"],
            "source_id": r["source_id"], "source_name": r["source_name"],
            "unit": r["unit"], "trade_date": r["trade_date"], "value": r["value"],
            "change": round(r["value"] - r["prev_v"], 4), "pct": round(pct, 2),
            "spark5": [float(x) for x in (r["spark5"] or "").split(",") if x],
        })
    return {"as_of_date": as_of, "movements": movements}
```

```python
# backend/app/api/jobs.py
from fastapi import APIRouter, Depends
from ..db import get_conn

router = APIRouter()

@router.get("/api/jobs/latest")
def jobs_latest(conn=Depends(get_conn)):
    rows = conn.execute(
        """SELECT j.* FROM job_runs j
             JOIN (SELECT source_id, MAX(id) mid FROM job_runs
                    GROUP BY source_id) t ON t.mid=j.id""").fetchall()
    return {"jobs": [dict(r) for r in rows]}
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_api_movements.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/ backend/tests/test_api_movements.py && git commit -m "feat: 今日异动榜（阈值过滤/降序/5日sparkline）+ 抓取状态端点"
```

---

### Task 13: 前端工程骨架（Vite + Vue3 + ECharts）

**Files:**
- Create: `frontend/package.json`（替换占位）、`frontend/vite.config.js`、`frontend/index.html`、`frontend/src/main.js`、`frontend/src/router.js`、`frontend/src/App.vue`、`frontend/src/api.js`、`frontend/src/format.js`
- Test: `frontend/src/format.test.js`

- [ ] **Step 1: 写 package.json 与配置**

```json
{
  "name": "dtp-frontend", "private": true, "version": "0.1.0", "type": "module",
  "scripts": { "dev": "vite", "build": "vite build", "test": "vitest run" },
  "dependencies": {
    "vue": "^3.5.0", "vue-router": "^4.4.0",
    "echarts": "^6.0.0", "vue-echarts": "^8.0.0"
  },
  "devDependencies": { "vite": "^7.0.0", "@vitejs/plugin-vue": "^6.0.0", "vitest": "^3.0.0" }
}
```

```javascript
// frontend/vite.config.js
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: { environment: "node" },
});
```

```html
<!-- frontend/index.html -->
<!doctype html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>数据追踪平台</title></head>
<body><div id="app"></div><script type="module" src="/src/main.js"></script></body>
</html>
```

- [ ] **Step 2: 写入口、路由、api 封装**

```javascript
// frontend/src/main.js
import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
createApp(App).use(router).mount("#app");
```

```javascript
// frontend/src/router.js
import { createRouter, createWebHistory } from "vue-router";
import OverviewView from "./views/OverviewView.vue";
import SourceDetailView from "./views/SourceDetailView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: OverviewView },
    { path: "/source/:id", component: SourceDetailView, props: true },
  ],
});
```

```javascript
// frontend/src/api.js
const j = (r) => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); };

export const api = {
  sources: () => fetch("/api/sources").then(j),
  sourceDetail: (id, range = "3m") => fetch(`/api/sources/${id}?range=${range}`).then(j),
  movements: () => fetch("/api/movements").then(j),
  jobs: () => fetch("/api/jobs/latest").then(j),
  series: (instruments, { normalize = 0, range = "6m" } = {}) =>
    fetch(`/api/series?instruments=${instruments.join(",")}&normalize=${normalize}&range=${range}`).then(j),
};
```

- [ ] **Step 3: 写 format.js（纯函数，TDD）**

先写测试：

```javascript
// frontend/src/format.test.js
import { describe, it, expect } from "vitest";
import { pctColor, sparkBars, fmtVal, fmtChange } from "./format.js";

describe("pctColor（红涨绿跌）", () => {
  it("正数涨红、负数跌绿、零灰", () => {
    expect(pctColor(1.2)).toBe("up");
    expect(pctColor(-0.5)).toBe("down");
    expect(pctColor(0)).toBe("flat");
  });
});

describe("sparkBars（5日迷你趋势）", () => {
  it("映射为块状字符", () => {
    expect(sparkBars([1, 2, 3, 4, 5])).toBe("▁▃▄▆█");
    expect(sparkBars([])).toBe("");
  });
  it("全等值不除零", () => {
    expect(sparkBars([2, 2, 2])).toBe("▄▄▄");
  });
});

describe("fmtVal / fmtChange", () => {
  it("亿份保留两位", () => {
    expect(fmtVal(254.849, "亿份")).toBe("254.85 亿份");
  });
  it("变化带符号", () => {
    expect(fmtChange(7.95, 3.22)).toBe("+7.95 (+3.22%)");
    expect(fmtChange(-0.18, -2.69)).toBe("-0.18 (-2.69%)");
  });
});
```

Run: `cd frontend && npm install && npx vitest run`（`npm install` 在容器里跑：`docker run --rm -v $PWD/frontend:/fe -w /fe node:20-slim npm install`，生成 package-lock.json 一并提交）
Expected: FAIL（format.js 不存在）

再写实现：

```javascript
// frontend/src/format.js
export const UP = "#e74c3c", DOWN = "#27ae60", FLAT = "#95a5a6";

export function pctColor(pct) {
  if (pct > 0) return "up";
  if (pct < 0) return "down";
  return "flat";
}

const BARS = "▁▂▃▄▅▆▇█";
export function sparkBars(vals) {
  if (!vals || vals.length === 0) return "";
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  return vals.map(v => BARS[Math.round(((v - min) / span) * (BARS.length - 1))]).join("");
}

export function fmtVal(v, unit) {
  if (v == null) return "—";
  const n = Math.abs(v) >= 1000 ? v.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
                                : v.toFixed(2);
  return `${n} ${unit ?? ""}`.trim();
}

export function fmtChange(change, pct) {
  if (change == null) return "—";
  const sign = change > 0 ? "+" : "";
  return `${sign}${change.toFixed(2)} (${sign}${(pct ?? 0).toFixed(2)}%)`;
}
```

Run: `cd frontend && npx vitest run` → Expected: 5 passed

- [ ] **Step 4: 写 App.vue（全局布局 + 深色侧边栏）**

```vue
<!-- frontend/src/App.vue -->
<script setup>
import { ref, onMounted } from "vue";
import { api } from "./api";
const groups = ref([]);
onMounted(async () => { groups.value = await api.sources(); });
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">数据追踪<span>ETF · 大宗商品</span></div>
      <nav>
        <RouterLink class="nav-item overview" to="/">📊 总览</RouterLink>
        <div v-for="g in groups" :key="g.group">
          <div class="group-label">{{ g.group }}</div>
          <RouterLink v-for="s in g.sources" :key="s.id" class="nav-item" :to="`/source/${s.id}`">
            <span class="dot" />{{ s.name }}
          </RouterLink>
        </div>
      </nav>
    </aside>
    <main class="main"><RouterView /></main>
  </div>
</template>

<style>
:root { --up:#e74c3c; --down:#27ae60; --sidebar:#1e2430; --sidebar-active:#c0392b; }
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, "PingFang SC", "Microsoft YaHei", sans-serif; background:#f5f6f8; }
.layout { display: flex; min-height: 100vh; }
.sidebar { width: 210px; background: var(--sidebar); color: #aab; padding: 16px 10px; flex-shrink: 0; }
.brand { color: #fff; font-weight: 700; font-size: 18px; margin-bottom: 4px; }
.brand span { display:block; color:#667; font-size: 11px; font-weight: 400; }
.group-label { color:#556; font-size: 11px; margin: 14px 8px 4px; }
.nav-item { display:block; color:#aab; text-decoration:none; padding:7px 10px; border-radius:5px; font-size:13px; }
.nav-item.router-link-active { background: var(--sidebar-active); color:#fff; }
.nav-item.overview { margin-bottom: 6px; }
.dot { display:inline-block; width:7px; height:7px; border-radius:50%; background:#3a4a5f; margin-right:7px; }
.main { flex:1; padding: 18px; }
.up { color: var(--up); } .down { color: var(--down); } .flat { color: #95a5a6; }
.card { background:#fff; border-radius:8px; padding:14px 16px; }
.banner { background:#c0392b; color:#fff; padding:12px 18px; border-radius:8px; font-weight:700; }
.badge { background:rgba(255,255,255,.25); border-radius:10px; padding:2px 10px; font-size:12px; font-weight:400; margin-left:8px; }
</style>
```

- [ ] **Step 5: 建两个占位视图（Task 14/16 实装）**

```vue
<!-- frontend/src/views/OverviewView.vue（占位） -->
<template><div class="card">总览加载中…</div></template>
```

```vue
<!-- frontend/src/views/SourceDetailView.vue（占位） -->
<template><div class="card">详情加载中…</div></template>
```

- [ ] **Step 6: 容器内构建验证（环境隔离）**

Run: `docker run --rm -v $PWD/frontend:/fe -w /fe node:20-slim sh -c "npm ci && npm run build && npx vitest run"`
Expected: 构建产出 `frontend/dist/`；vitest 5 passed

- [ ] **Step 7: Commit**

```bash
git add frontend/ && git commit -m "feat: 前端骨架（Vite+Vue3+Router+深色侧边栏布局+格式化函数TDD）"
```

---

### Task 14: 数据源详情页（卡片行 + 趋势图 + 时间范围）

**Files:**
- Create: `frontend/src/components/TrendChart.vue`、`frontend/src/components/CardRow.vue`、`frontend/src/components/JobStatus.vue`
- Modify: `frontend/src/views/SourceDetailView.vue`（替换占位）

- [ ] **Step 1: 写 TrendChart.vue（vue-echarts 封装）**

```vue
<!-- frontend/src/components/TrendChart.vue -->
<script setup>
import { computed } from "vue";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from "echarts/components";
import VChart from "vue-echarts";
use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent]);

const props = defineProps({
  series: { type: Array, default: () => [] }, // [{name, data: [[date, v],...]}]
  unit: String,
  percent: Boolean,                            // 归一化模式时 Y 轴单位 %
});
const PALETTE = ["#e74c3c", "#3498db", "#f39c12", "#27ae60", "#9b59b6", "#16a085", "#8e44ad", "#d35400"];

const option = computed(() => ({
  color: PALETTE,
  tooltip: { trigger: "axis" },
  legend: { type: "scroll", top: 0 },
  grid: { left: 60, right: 20, top: 36, bottom: 56 },
  xAxis: { type: "time" },
  yAxis: { type: "value", scale: true,
           name: props.percent ? "%" : (props.unit ?? "") },
  dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 10 }],
  series: props.series.map(s => ({
    name: s.name, type: "line", showSymbol: false,
    connectNulls: true, data: s.data,
  })),
}));
</script>

<template>
  <VChart :option="option" autoresize style="height: 420px" />
</template>
```

- [ ] **Step 2: 写 CardRow.vue**

```vue
<!-- frontend/src/components/CardRow.vue -->
<script setup>
import { fmtVal, fmtChange, pctColor } from "../format.js";
defineProps({ cards: Array, unit: String });
</script>

<template>
  <div class="cards-row">
    <div v-for="c in cards" :key="c.instrument_id" class="inst-card">
      <div class="inst-name">{{ c.name }}<span v-if="c.code"> · {{ c.code }}</span></div>
      <div class="inst-value">{{ fmtVal(c.value, unit) }}</div>
      <div :class="pctColor(c.day_change ?? 0)" v-if="c.day_change !== null">
        {{ fmtChange(c.day_change, c.day_pct) }}
      </div>
      <div class="range-change" v-if="c.range_pct !== null" :class="pctColor(c.range_change ?? 0)">
        区间 {{ fmtChange(c.range_change, c.range_pct) }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.cards-row { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0; }
.inst-card { background:#fff; border-radius:8px; padding:12px 16px; min-width:170px; flex:1; cursor:default; }
.inst-name { color:#666; font-size:12px; }
.inst-value { font-size:20px; font-weight:700; margin:4px 0; }
.range-change { color:#999; font-size:11px; margin-top:2px; }
</style>
```

- [ ] **Step 3: 写 JobStatus.vue**

```vue
<!-- frontend/src/components/JobStatus.vue -->
<script setup>
import { ref, onMounted } from "vue";
import { api } from "../api";
const jobs = ref([]);
onMounted(async () => (await api.jobs()).jobs.forEach(j => jobs.value.push(j)));
</script>

<template>
  <span class="job-status">
    <template v-for="(j, i) in jobs" :key="i">
      <span :class="j.status === 'ok' ? 'flat' : 'down'">
        {{ j.source_id }}:{{ j.status }}</span><span v-if="i < jobs.length-1"> · </span>
    </template>
  </span>
</template>

<style scoped>
.job-status { font-size: 11px; color: #999; }
</style>
```

- [ ] **Step 4: 实装 SourceDetailView.vue**

```vue
<!-- frontend/src/views/SourceDetailView.vue -->
<script setup>
import { ref, watch, computed, onMounted } from "vue";
import { api } from "../api";
import JobStatus from "../components/JobStatus.vue";
import CardRow from "../components/CardRow.vue";
import TrendChart from "../components/TrendChart.vue";

const props = defineProps({ id: String });
const RANGES = [["1m","1月"],["3m","3月"],["6m","6月"],["1y","1年"],["all","全部"]];
const detail = ref(null);
const range = ref("3m");

async function load() {
  detail.value = await api.sourceDetail(props.id, range.value);
}
onMounted(load);
watch([() => props.id, range], load);

const chartSeries = computed(() =>
  Object.entries(detail.value?.series ?? {})
    .map(([iid, pts]) => ({ name: nameOf(iid), data: pts })));
const nameOf = (iid) =>
  detail.value?.cards.find(c => c.instrument_id === iid)?.name ?? iid;
</script>

<template>
  <div v-if="detail">
    <div class="banner">
      {{ detail.name }}
      <span class="badge">{{ detail.cards.length }} 项 · 数据截至 {{ detail.as_of_date ?? "累积中" }}</span>
      <JobStatus />
    </div>

    <div class="range-bar">
      <button v-for="[k, label] in RANGES" :key="k" :class="{ active: range === k }" @click="range = k">{{ label }}</button>
    </div>

    <CardRow :cards="detail.cards" :unit="detail.unit" />

    <div class="card" v-if="detail.as_of_date">
      <TrendChart :series="chartSeries" :unit="detail.unit" />
    </div>
    <div class="card empty" v-else>数据累积中，暂无历史序列</div>
  </div>
</template>

<style scoped>
.range-bar { margin: 12px 0 4px; display:flex; gap:6px; }
.range-bar button { border:1px solid #ddd; background:#fff; border-radius:4px; padding:4px 12px; cursor:pointer; font-size:12px; }
.range-bar button.active { background:#f0f3f6; color:#c0392b; font-weight:700; border-color:#c0392b; }
.empty { color:#999; text-align:center; padding:40px; }
</style>
```

- [ ] **Step 5: 构建冒烟**

Run: `docker run --rm -v $PWD/frontend:/fe -w /fe node:20-slim sh -c "npm ci && npm run build"`
Expected: 构建成功

- [ ] **Step 6: Commit**

```bash
git add frontend/ && git commit -m "feat: 详情页（卡片行+多线趋势图+时间范围+状态条）"
```

---

### Task 15: 总览首页（异动榜 + 分组速览）

**Files:**
- Create: `frontend/src/components/MovementsTable.vue`、`frontend/src/components/SourceTiles.vue`
- Modify: `frontend/src/views/OverviewView.vue`（替换占位）

- [ ] **Step 1: 写 MovementsTable.vue**

```vue
<!-- frontend/src/components/MovementsTable.vue -->
<script setup>
import { fmtVal, fmtChange, pctColor, sparkBars } from "../format.js";
defineProps({ movements: Array });
</script>

<template>
  <table class="mv-table">
    <thead><tr><th>数据源 / 品种</th><th class="num">最新值</th><th class="num">变化</th><th class="num">5日趋势</th></tr></thead>
    <tbody>
      <tr v-for="m in movements" :key="m.instrument_id">
        <td><RouterLink :to="`/source/${m.source_id}`" class="mv-link">
          {{ m.source_name }} · {{ m.instrument_name }}</RouterLink></td>
        <td class="num">{{ fmtVal(m.value, m.unit) }}</td>
        <td class="num" :class="pctColor(m.pct)">{{ fmtChange(m.change, m.pct) }}</td>
        <td class="num spark" :class="pctColor(m.pct)">{{ sparkBars(m.spark5) }}</td>
      </tr>
    </tbody>
  </table>
  <p v-if="!movements.length" class="none">今日无异动（未超各源阈值）</p>
</template>

<style scoped>
.mv-table { width:100%; border-collapse:collapse; font-size:13px; }
.mv-table th { color:#999; font-weight:400; text-align:left; padding:6px 8px; border-bottom:1px solid #eee; }
.mv-table td { padding:8px; border-bottom:1px solid #f3f3f3; }
.num { text-align:right; font-variant-numeric: tabular-nums; }
.mv-link { color:inherit; text-decoration:none; }
.mv-link:hover { color:#c0392b; }
.spark { letter-spacing: 2px; }
.none { color:#999; text-align:center; padding:24px; }
</style>
```

- [ ] **Step 2: 写 SourceTiles.vue**

```vue
<!-- frontend/src/components/SourceTiles.vue -->
<script setup>
defineProps({ groups: Array });   // 来自 /api/sources
</script>

<template>
  <div v-for="g in groups" :key="g.group" class="tile-group">
    <div class="tile-group-title" v-if="g.group">{{ g.group }}</div>
    <div class="tiles">
      <RouterLink v-for="s in g.sources" :key="s.id" class="tile"
                  :to="`/source/${s.id}`">
        <div class="tile-name">{{ s.name }}</div>
        <div class="tile-meta">{{ s.inst_count }} 项 · 数据截至 {{ s.latest_date ?? "累积中" }}</div>
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
.tile-group { margin-bottom: 12px; }
.tile-group-title { font-size:12px; color:#888; margin-bottom:6px; }
.tiles { display:flex; gap:10px; flex-wrap:wrap; }
.tile { background:#fff; border-radius:8px; padding:12px 16px; min-width:180px; text-decoration:none; color:inherit; border-left:3px solid #c0392b; }
.tile-name { font-weight:600; font-size:14px; }
.tile-meta { color:#999; font-size:11px; margin-top:4px; }
</style>
```

- [ ] **Step 3: 实装 OverviewView.vue**

```vue
<!-- frontend/src/views/OverviewView.vue -->
<script setup>
import { ref, onMounted } from "vue";
import { api } from "../api";
import MovementsTable from "../components/MovementsTable.vue";
import SourceTiles from "../components/SourceTiles.vue";

const movements = ref([]);
const groups = ref([]);
const asOf = ref(null);
const jobCount = ref(0);

onMounted(async () => {
  const [mv, src] = await Promise.all([api.movements(), api.sources()]);
  movements.value = mv.movements;
  asOf.value = mv.as_of_date;
  groups.value = src.flatMap(g => g.sources);
});
</script>

<template>
  <div class="banner">总览<span class="badge">数据截至 {{ asOf ?? "—" }}</span></div>
  <div class="card sec">
    <h3>⚡ 今日异动 <small>跨全部数据源 · 按变化幅度排序</small></h3>
    <MovementsTable :movements="movements" />
  </div>
  <div class="card sec">
    <h3>分组速览</h3>
    <SourceTiles :groups="[{ group: '', sources: groups }]" />
  </div>
</template>

<style scoped>
.sec { margin-top: 14px; }
h3 small { color:#999; font-size:11px; font-weight:400; margin-left:8px; }
</style>
```

- [ ] **Step 4: 构建冒烟**

Run: `docker run --rm -v $PWD/frontend:/fe -w /fe node:20-slim sh -c "npm ci && npm run build"`
Expected: 构建成功

- [ ] **Step 5: Commit**

```bash
git add frontend/ && git commit -m "feat: 总览首页（异动榜+分组速览）"
```

---

## M3 · 对比与质量

### Task 16: /api/series 归一化实装

**Files:**
- Modify: `backend/app/api/series.py`（替换占位）
- Test: `backend/tests/test_api_series.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_api_series.py
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

@pytest.fixture
def client(conn):
    import backend.app.db as dbm
    app.dependency_overrides[dbm.get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def cross_source(conn):
    # 510300(share) 与 coal-price(price)，量纲不同
    for iid, d, v in [("510300","2026-10-13",100),("510300","2026-10-14",110),("510300","2026-10-15",120),
                      ("coal-price","2026-10-13",800),("coal-price","2026-10-14",820),("coal-price","2026-10-15",880)]:
        conn.execute("INSERT INTO observations VALUES(?,?,?,NULL,'t')", (iid, "share" if iid.startswith("5") else "price", d, v))
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
    assert coal["points"][-1][1] == pytest.approx(110.0)

def test_series_expands_source_id(client, cross_source):
    # 传 source id（如 hs300-etf）自动展开为该源全部 instruments
    body = client.get("/api/series?instruments=hs300-etf&range=1m").json()
    assert len(body["series"]) == 1
    assert body["series"][0]["instrument_id"] == "510300"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest backend/tests/test_api_series.py -v`
Expected: FAIL（占位返回空）

- [ ] **Step 3: 实装 series.py**

```python
# backend/app/api/series.py（完整替换）
from fastapi import APIRouter, Depends, Query
from ..db import get_conn

router = APIRouter()
RANGE_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "all": None}

def _resolve_ids(conn, ids: list[str]) -> list[str]:
    """入参同时支持 instrument id 与 source id（source 自动展开为该源全部 instruments）。"""
    out = []
    for i in ids:
        if conn.execute("SELECT 1 FROM instruments WHERE id=?", (i,)).fetchone():
            out.append(i)
        else:
            out += [r["id"] for r in conn.execute(
                "SELECT id FROM instruments WHERE source_id=?", (i,)).fetchall()]
    return out

@router.get("/api/series")
def series(instruments: str = Query(""), normalize: int = 0,
           range: str = Query("6m"), conn=Depends(get_conn)):
    ids = _resolve_ids(conn, [i for i in instruments.split(",") if i])
    if not ids:
        return {"as_of_date": None, "series": []}
    days = RANGE_DAYS.get(range)
    ph = ",".join("?" * len(ids))
    as_of = conn.execute(
        f"SELECT MAX(trade_date) m FROM observations WHERE instrument_id IN ({ph})",
        ids).fetchone()["m"]
    if not as_of:
        return {"as_of_date": None, "series": []}
    where_days = f"AND trade_date >= date('{as_of}', '-{days} days')" if days else ""
    if normalize:
        sql = f"""SELECT instrument_id, trade_date, value,
                         FIRST_VALUE(value) OVER (PARTITION BY instrument_id ORDER BY trade_date) base
                    FROM observations
                   WHERE instrument_id IN ({ph}) AND value IS NOT NULL {where_days}"""
        raw = conn.execute(sql, ids).fetchall()
        grouped: dict[str, list] = {}
        for r in raw:
            if not r["base"]:
                continue
            grouped.setdefault(r["instrument_id"], []).append(
                [r["trade_date"], round(r["value"] / r["base"] * 100, 2)])
    else:
        raw = conn.execute(
            f"""SELECT instrument_id, trade_date, value FROM observations
                 WHERE instrument_id IN ({ph}) {where_days} ORDER BY trade_date""", ids).fetchall()
        grouped = {}
        for r in raw:
            grouped.setdefault(r["instrument_id"], []).append([r["trade_date"], r["value"]])
    names = {r["id"]: r["name"] for r in conn.execute(
        f"SELECT id, name FROM instruments WHERE id IN ({ph})", ids).fetchall()}
    return {"as_of_date": as_of,
            "series": [{"instrument_id": iid, "name": names.get(iid, iid), "points": pts}
                       for iid, pts in grouped.items()]}
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest backend/tests/test_api_series.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/series.py backend/tests/test_api_series.py && git commit -m "feat: /api/series 跨源取数（支持 source id 展开）+ 期初基线归一化"
```

---

### Task 17: 对比 chips 与详情页集成

**Files:**
- Create: `frontend/src/components/CompareChips.vue`
- Modify: `frontend/src/views/SourceDetailView.vue`

- [ ] **Step 1: 写 CompareChips.vue**

```vue
<!-- frontend/src/components/CompareChips.vue -->
<script setup>
import { ref, computed } from "vue";
import { api } from "../api";

const props = defineProps({ modelValue: Array, excludeIds: Array });
const emit = defineEmits(["update:modelValue"]);
const picking = ref(false);
const allSources = ref([]);

async function openPicker() {
  picking.value = !picking.value;
  if (picking.value && !allSources.value.length)
    allSources.value = (await api.sources()).flatMap(g => g.sources);
}
const candidates = computed(() =>
  allSources.value
    .filter(s => !props.excludeIds.includes(s.id))
    .flatMap(s => s.inst_count ? [{ id: s.id, name: s.name }] : []));
// 候选粒度：数据源级（取其全部成分序列）
const add = (sid) => {
  if (!props.modelValue.includes(sid)) emit("update:modelValue", [...props.modelValue, sid]);
  picking.value = false;
};
const remove = (sid) =>
  emit("update:modelValue", props.modelValue.filter(x => x !== sid));
</script>

<template>
  <div class="chips">
    <span v-for="sid in modelValue" :key="sid" class="chip added" @click="remove(sid)">
      {{ sid }} ✕
    </span>
    <span class="chip add-btn" @click="openPicker">+ 添加对比 ▾</span>
    <div v-if="picking" class="picker">
      <div v-for="c in candidates" :key="c.id" class="picker-item" @click="add(c.id)">{{ c.name }}</div>
    </div>
  </div>
</template>

<style scoped>
.chips { display:flex; gap:6px; flex-wrap:wrap; position:relative; align-items:center; }
.chip { border:1px dashed #999; border-radius:12px; padding:2px 10px; font-size:12px; cursor:pointer; }
.chip.added { border-style:solid; background:#eaf4fd; color:#2980b9; border-color:#3498db; }
.add-btn:hover { border-color:#c0392b; color:#c0392b; }
.picker { position:absolute; top:28px; left:0; background:#fff; border:1px solid #ddd; border-radius:6px; z-index:10; min-width:180px; box-shadow:0 4px 12px rgba(0,0,0,.08); }
.picker-item { padding:8px 12px; cursor:pointer; font-size:13px; }
.picker-item:hover { background:#f5f6f8; }
</style>
```

- [ ] **Step 2: 集成进 SourceDetailView.vue（替换 script 与图表区）**

`<script setup>` 部分替换为：

```javascript
import { ref, watch, computed, onMounted } from "vue";
import { api } from "../api";
import JobStatus from "../components/JobStatus.vue";
import CardRow from "../components/CardRow.vue";
import TrendChart from "../components/TrendChart.vue";
import CompareChips from "../components/CompareChips.vue";

const props = defineProps({ id: String });
const RANGES = [["1m","1月"],["3m","3月"],["6m","6月"],["1y","1年"],["all","全部"]];
const detail = ref(null);
const range = ref("3m");
const compareIds = ref([]);      // 添加对比的数据源 id 列表
const allSeries = ref([]);       // /api/series 返回（own + compare 统一取数，归一化时同基准）
const normalize = ref(0);

async function load() {
  detail.value = await api.sourceDetail(props.id, range.value);
  await loadSeries();
}
async function loadSeries() {
  const ownIds = Object.keys(detail.value?.series ?? {});
  const ids = [...ownIds, ...compareIds.value];
  if (!ids.length) { allSeries.value = []; return; }
  allSeries.value = (await api.series(ids, { normalize: normalize.value, range: range.value })).series;
}
onMounted(load);
watch([() => props.id, range], load);
watch([compareIds, normalize], loadSeries);

const nameOf = (iid) =>
  detail.value?.cards.find(c => c.instrument_id === iid)?.name ?? iid;
const chartSeries = computed(() =>
  allSeries.value.map(s => ({ name: s.name, data: s.points })));
```

模板中 `<CardRow>` 与图表之间插入对比工具条：

```html
<div class="compare-bar">
  <CompareChips v-model="compareIds" :exclude-ids="[id]" />
  <label class="norm-toggle">
    <input type="checkbox" :checked="normalize === 1" @change="normalize = $event.target.checked ? 1 : 0">
    归一化（期初=100）
  </label>
</div>
```

style 追加：

```css
.compare-bar { display:flex; gap:14px; align-items:center; margin:10px 0; }
.norm-toggle { font-size:12px; color:#666; }
```

- [ ] **Step 3: 构建冒烟**

Run: `docker run --rm -v $PWD/frontend:/fe -w /fe node:20-slim sh -c "npm ci && npm run build"`
Expected: 构建成功

- [ ] **Step 4: Commit**

```bash
git add frontend/ && git commit -m "feat: 详情页对比 chips + 归一化开关（跨源叠加）"
```

---

### Task 18: 真实数据回补与端到端验收

**Files:**
- 无新文件（部署验证任务）

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q && docker compose run --rm collect uv run --no-sync python -m pytest -q`
Expected: 全部通过（容器内外一致）

- [ ] **Step 2: 构建并启动 Web 容器**

Run: `docker compose up -d --build web`
Expected: `docker compose ps` 显示 web Up；`curl -s http://localhost:8000/api/sources` 返回三源 JSON

- [ ] **Step 3: 真实数据回补（网络）**

Run: `docker compose run --rm collect uv run --no-sync python -m backend.collect --backfill --from 2026-01-01 --source hs300-etf`
Expected: 日志显示逐交易日入库；`curl -s "http://localhost:8000/api/sources/hs300-etf?range=all"` 的 `as_of_date` 为最近可得交易日（T+1）

Run: `docker compose run --rm collect uv run --no-sync python -m backend.collect`
Expected: 三源均执行；煤炭价格允许 failed（爬虫属 M1 后半程/M2，见设计文档 §5.1，若受阻不阻塞验收，其余两源必须 ok）

- [ ] **Step 4: 浏览器验收（对照 PRD §7 六条成功标准）**

打开 `http://<内网IP>:8000`，逐条核对：
1. 无需登录直接可用 ✅
2. 详情页数据截至标注为实际交易日（T+1）✅
3. 总览异动榜可下钻详情 ✅
4. 沪深300 详情：5 卡片 + 份额曲线 + 添加"煤炭价格/电解铜库存"对比 + 归一化切换 ✅
5. `docker compose run --rm collect` 连续跑两遍，数据不重不漏（sqlite count 不变）✅
6. （SOP 验证）按设计文档 §12 口头推演新增中证500 步骤 ✅

- [ ] **Step 5: 收尾提交**

```bash
git add -A && git commit -m "chore: 端到端验收通过（真实数据回补+浏览器六项标准）" --allow-empty
```

---

## 任务依赖关系

- Task 1→2→3 串行；Task 4 需网络（一次性）；Task 5、6 依赖 2/3/4；Task 7 依赖 3/5/6；Task 8 依赖 7；Task 9 依赖 1-8（镜像内跑全量测试）；Task 10 依赖 9
- Task 11 依赖 2；Task 12 依赖 11；Task 13 可与 Task 11 并行（仅依赖 Docker）；Task 14 依赖 11/13；Task 15 依赖 12/13；Task 16 依赖 11；Task 17 依赖 14/16；Task 18 依赖全部
- 煤炭价格 fetcher（`scrapers.coal_price`）按设计文档 §5.1 排在 M1 后半程至 M2 之间实施：先用浏览器/`curl` 手工探查 CCTD/生意社页面结构并录制 fixture，再按 Task 5/6 同样的 TDD 节奏实现；若遇登录墙/反爬，允许顺延（不用近似品种替代），不阻塞 Task 9-18
