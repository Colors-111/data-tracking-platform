# v1.4 融资融券源实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `margin`「融资融券」源（新分组「市场资金」）：11 instruments（沪深 8 余额类 + 全市场 3 杠杆状况类），三上游接口全历史一次拉取 + 交易所直连交叉校验，前端详情页 5 分区。

**Architecture:** 新 fetcher `akshare.margin`（`backend/fetchers/akshare_margin.py`）编排乐咕双接口 + 中证金融账户统计（分支级隔离，复用 `run_branches`）；交易所直连 `stock_margin_sse/szse` 只做交叉校验（>0.5% 相对差告警，失败/远古目标日静默跳过）。全历史型语义（fetch 忽略 trade_date 全量返回、幂等 upsert），回补总成本 3 次请求。前端仅扩 `metricSections.js`（分区定义数据驱动，SourceDetailView 零改动）。

**Tech Stack:** Python 3.12 + akshare 1.19.x + SQLite；Vue3 + vitest。设计依据：`docs/superpowers/specs/2026-10-16-data-tracking-platform-design.md` §5.4。

**实测数据契约（2026-10-21 录制，写代码前必读）：**
- 乐咕 `macro_china_market_margin_sh/sz()`：列 `['日期','融资买入额','融资余额','融券卖出量','融券余量','融券余额','融资融券余额']`，日期列 fixture 落盘为 epoch-ms int64（normalize_date epoch 分支归一，同 SPDR 先例）、生产为日期对象/str，金额单位**元**（沪最新融资余额 ≈1.26e12），最新到当日晚间
- `stock_margin_account_info()`：列 `['日期','融资余额','融券余额','融资买入额','融券卖出额','证券公司数量','营业部数量','个人投资者数量','机构投资者数量','参与交易的投资者数量','有融资融券负债的投资者数量','担保物总价值','平均维持担保比例']`，2012-09-27 起；**余额/担保物单位亿元、维持担保比例 %**；「个人投资者数量」单位万而「参与交易的投资者数量」单位**个**（本设计只用后者，÷1e4 → 万人）；担保物/投资者数 2012-09-27→2015-04-22 为 NaN（首行 null，首个非 null 2015-04-23）
- `stock_margin_sse(start_date, end_date)`（YYYYMMDD str）：列 `['信用交易日期','融资余额','融资买入额','融券余量','融券余量金额','融券卖出量','融资融券余额']`，日期 `YYYYMMDD` str（normalize_date 已支持 8 位数字串），单位元，「融券余量金额」即融券余额；时效滞后约 5 个交易日
- `stock_margin_szse(date)`（YYYYMMDD str）：列 `['融资买入额','融资余额','融券卖出量','融券余量','融券余额','融资融券余额']`（akshare 源码固化），单位元；本环境深交所 Errno 101 断连频发（校验静默跳过即可）

---

### Task 1: 录制三接口真实 fixture

**Files:**
- Create: `backend/tests/gen_margin_fixtures.py`
- Create: `backend/tests/fixtures/margin_lg_sh.json`、`margin_lg_sz.json`、`margin_account.json`（运行脚本产出）

- [ ] **Step 1: 写录制脚本**

```python
"""录制融资融券三上游接口真实样本（v1.4 设计 §5.4）。
   运行：uv run --no-sync python backend/tests/gen_margin_fixtures.py
   全量录制（对齐 macro fixture 惯例：LME 2718 行同样全量入库）。"""
from pathlib import Path

import akshare as ak

FIX = Path(__file__).parent / "fixtures"


def dump(df, name):
    df.to_json(FIX / name, orient="records", force_ascii=False, indent=1)
    print(name, len(df), "rows")


dump(ak.macro_china_market_margin_sh(), "margin_lg_sh.json")
dump(ak.macro_china_market_margin_sz(), "margin_lg_sz.json")
dump(ak.stock_margin_account_info(), "margin_account.json")
```

- [ ] **Step 2: 运行录制**

Run: `uv run --no-sync python backend/tests/gen_margin_fixtures.py`
Expected: 三行输出（沪 ≈4015 rows、深 ≈3817 rows、账户 ≈3410 rows）；若某接口网络失败，重试一次，仍失败则停（真实样本是后续测试前提）。

- [ ] **Step 3: 抽查 fixture 内容**

Run: `head -c 400 backend/tests/fixtures/margin_lg_sh.json`
Expected: 含 `"日期"` 与 `"融资余额"` 字段。⚠️ 实测（Task 1 交付）：`"日期"` 落盘为 **epoch-ms int64**（akshare 返回 datetime.date 对象、to_json 默认 date_format='epoch'），非 "YYYY-MM-DD" 字符串——`pd.read_json` 无参回读仍是 int64，必须经 `normalize_date` 归一（UTC 锚定 epoch-ms 分支，同 SPDR/macro fixture 先例，往返无损）。`margin_account.json` 首行（2012-09-27）的 `担保物总价值`/`参与交易的投资者数量` 为 null（早期缺失，供 NaN 跳行测试；首个非 null 在 2015-04-23）。

- [ ] **Step 4: Commit**

```bash
git add backend/tests/gen_margin_fixtures.py backend/tests/fixtures/margin_lg_sh.json backend/tests/fixtures/margin_lg_sz.json backend/tests/fixtures/margin_account.json
git commit -m "test: 录制融资融券三接口真实 fixture（乐咕沪/深+账户统计）"
```

---

### Task 2: seed 注册 margin 源与 11 仪器

**Files:**
- Modify: `backend/seed.sql`（文件尾部追加）
- Modify: `backend/tests/test_db.py`（源数 14→15、margin 仪器断言）

- [ ] **Step 1: 写失败测试（更新 test_db.py 计数断言）**

`test_seed_loaded` 中 `assert ... COUNT(*) c FROM sources ... == 14` 改为 `== 15`（注释同步"v1.4 融资融券"），文件末尾追加：

```python
def test_margin_source_seeded(conn):
    # v1.4 融资融券（设计 §5.4）：8 沪深余额类 + 3 全市场杠杆状况类，
    # 新分组「市场资金」驱动侧边栏自动出现；混合单位由 instrument 级 unit 承载
    src = conn.execute(
        "SELECT name, group_name, fetcher, unit, movement_th, lag_days"
        " FROM sources WHERE id='margin'").fetchone()
    assert src["name"] == "融资融券"
    assert src["group_name"] == "市场资金"
    assert src["fetcher"] == "akshare.margin"
    assert src["unit"] == "混合" and src['movement_th'] == 2.0 and src["lag_days"] == 1
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
```

同时 `test_init_db_idempotent` 的 `== 14` 改 `== 15`。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync python -m pytest backend/tests/test_db.py -q`
Expected: `test_margin_source_seeded` FAIL（No row 'margin'）+ 两处计数断言 FAIL（14≠15）；其余 PASS。

- [ ] **Step 3: seed.sql 追加源与仪器**

文件尾部追加：

```sql

-- ============ v1.4 融资融券（设计 §5.4） ============
-- 市场杠杆资金追踪：沪深分开余额类（乐咕，元→亿元）+ 全市场杠杆状况类
-- （中证金融账户统计：担保比例 %、担保物 亿元、参与交易投资者数 个→万人）。
-- 全历史一次拉取（回补 3 请求）；当日晚间更新当日 → 16:40 档 empty、次日 09:00 补。
INSERT OR IGNORE INTO sources(id,name,group_name,freq,fetcher,unit,movement_th,lag_days,enabled,sort_order) VALUES
 ('margin','融资融券','市场资金','daily','akshare.margin','混合',2.0,1,1,40);
INSERT OR IGNORE INTO instruments(id,source_id,code,metric,unit,name,sort_order) VALUES
 ('margin-sh-financing','margin','MARGIN-SH-FINANCING','financing','亿元','沪市融资余额',1),
 ('margin-sh-securities','margin','MARGIN-SH-SECURITIES','securities','亿元','沪市融券余额',2),
 ('margin-sh-total','margin','MARGIN-SH-TOTAL','total','亿元','沪市两融余额',3),
 ('margin-sh-buying','margin','MARGIN-SH-BUYING','buying','亿元','沪市融资买入额',4),
 ('margin-sz-financing','margin','MARGIN-SZ-FINANCING','financing','亿元','深市融资余额',5),
 ('margin-sz-securities','margin','MARGIN-SZ-SECURITIES','securities','亿元','深市融券余额',6),
 ('margin-sz-total','margin','MARGIN-SZ-TOTAL','total','亿元','深市两融余额',7),
 ('margin-sz-buying','margin','MARGIN-SZ-BUYING','buying','亿元','深市融资买入额',8),
 ('margin-maintenance','margin','MARGIN-MAINTENANCE','maintenance','%','平均维持担保比例(全市场)',9),
 ('margin-collateral','margin','MARGIN-COLLATERAL','collateral','亿元','两融担保物总价值(全市场)',10),
 ('margin-investors','margin','MARGIN-INVESTORS','investors','万人','参与交易投资者数(全市场)',11);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync python -m pytest backend/tests/test_db.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/seed.sql backend/tests/test_db.py
git commit -m "feat: seed 注册 margin 融资融券源（市场资金组，11 仪器）"
```

---

### Task 3: fetcher 骨架 + 乐咕主通道（沪深 8 仪器）

**Files:**
- Create: `backend/fetchers/akshare_margin.py`
- Create: `backend/tests/test_fetcher_margin.py`

- [ ] **Step 1: 写失败测试（fixture 环境 + 编排/换算/fail-loud 契约）**

```python
"""融资融券 fetcher 测试（v1.4 设计 §5.4）：三接口编排、元→亿元换算、
   万人换算、早期 NaN 跳行、分支隔离、交叉校验三态（含远古节流）、fail-loud。"""
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from backend.fetchers.base import build_fetcher

FIX = Path(__file__).parent / "fixtures"

# fixture 最新日期（录制日 2026-10-21 附近）；校验节流测试用它注入确定"今天"
FIX_LATEST = "2026-10-20"


def _raise(msg):
    return lambda *a, **k: (_ for _ in ()).throw(RuntimeError(msg))


@pytest.fixture
def margin_env(conn, monkeypatch):
    src = conn.execute("SELECT * FROM sources WHERE id='margin'").fetchone()
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id='margin' ORDER BY sort_order"
    ).fetchall()
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sh",
                        lambda: pd.read_json(FIX / "margin_lg_sh.json"))
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sz",
                        lambda: pd.read_json(FIX / "margin_lg_sz.json"))
    monkeypatch.setattr(mod.ak, "stock_margin_account_info",
                        lambda: pd.read_json(FIX / "margin_account.json"))
    # 校验通道默认断连（走静默路径）——校验专项测试单独覆盖
    monkeypatch.setattr(mod.ak, "stock_margin_sse", _raise("SSE net down"))
    monkeypatch.setattr(mod.ak, "stock_margin_szse", _raise("SZSE net down"))
    monkeypatch.setattr(mod, "_today", lambda: dt.date.fromisoformat(FIX_LATEST))
    return build_fetcher(src["fetcher"], src, insts)


def test_unknown_code_fails_loud(conn):
    # 构建期未知 code raise（seed 拼错当场暴露，禁静默丢数据）
    src = conn.execute("SELECT * FROM sources WHERE id='margin'").fetchone()
    insts = [{"id": "x", "code": "MARGIN-XX-FOO", "metric": "financing"}]
    from backend.fetchers.akshare_margin import MarginFetcher
    with pytest.raises(ValueError, match="未支持的仪器代码"):
        MarginFetcher(src, insts)


def test_lg_produces_yi(margin_env):
    # 乐咕元 → ÷1e8 亿元：fixture 尾行沪融资余额 ≈1.26e12 元 ≈ 12580 亿
    obs = margin_env.fetch(FIX_LATEST)
    by = {(o.instrument_id, o.trade_date): o.value for o in obs}
    sh_fin = [v for (iid, d), v in by.items()
              if iid == "margin-sh-financing" and d == FIX_LATEST]
    assert sh_fin and 12000 < sh_fin[0] < 13500        # 亿元量级（2026-10 实测）
    assert ("margin-sz-financing", FIX_LATEST) in by   # 深市同日也在


def test_full_history_ignores_trade_date(margin_env):
    # 全历史型契约：fetch 忽略 trade_date 全量返回（backfill 首日全量入库的门）
    obs = margin_env.fetch("2020-01-02")
    dates = {o.trade_date for o in obs}
    assert len(dates) > 100 and "2020-01-02" in dates


def test_branch_isolation(margin_env, monkeypatch, caplog):
    # 乐咕 SH 挂 → SZ + 账户统计照常产出（run_branches 契约）
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sh", _raise("SH down"))
    obs = margin_env.fetch(FIX_LATEST)
    iids = {o.instrument_id for o in obs}
    assert "margin-sz-financing" in iids and "margin-sh-financing" not in iids
    assert "lg_sh" in caplog.text


def test_all_branches_fail_reraises(margin_env, monkeypatch):
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sh", _raise("a"))
    monkeypatch.setattr(mod.ak, "macro_china_market_margin_sz", _raise("b"))
    monkeypatch.setattr(mod.ak, "stock_margin_account_info", _raise("c"))
    with pytest.raises(RuntimeError, match="全部分支失败"):
        margin_env.fetch(FIX_LATEST)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-sync python -m pytest backend/tests/test_fetcher_margin.py -q`
Expected: FAIL/ERROR（ModuleNotFoundError: akshare_margin）。

- [ ] **Step 3: 实现 fetcher 骨架与乐咕通道**

```python
"""融资融券源（v1.4 设计 §5.4）：三上游主通道 + 交易所直连交叉校验。

   主通道（入库，全历史一次拉取、幂等 upsert）：
   - 乐咕 macro_china_market_margin_{sh,sz}：沪深分开余额类宽表（2010-03 起，
     当日晚间更新当日；金额单位元 → ÷1e8 入库亿元）
   - 中证金融 stock_margin_account_info：全市场账户统计（2012-09 起；余额/担保物
     单位亿元原样、维持担保比例 % 原样、参与交易投资者数 个 → ÷1e4 万人；
     担保物/投资者数 2015 年前后起有值，早期 NaN 跳行如实缺失）

   校验通道（只告警不落库）：交易所直连 stock_margin_sse（区间）/stock_margin_szse
   （单日）——同日同指标相对差 >0.5% 告警；直连失败/无数据/远古目标日（距运行日
   >30 日历日：backfill 逐日校验需数千请求，近期段持续校验已覆盖设计意图）静默跳过。

   三接口分支级隔离（run_branches）：单接口挂不拖死其余，全部失败才抛；
   ValueError（缺列等契约错误）不隔离（fail-loud）。fetch 忽略 trade_date
   全量返回——backfill 首目标日全量入库后 covered 门控自然续跑（零逐日请求）。"""
import datetime as dt
import logging
from functools import partial

import akshare as ak
import pandas as pd

from ._util import col, normalize_date, run_branches
from .base import Observation, register

log = logging.getLogger(__name__)

# 仪器 code 后缀 → (上游列名, 换算除数, metric)
LG_META = {
    "FINANCING": ("融资余额", 1e8, "financing"),
    "SECURITIES": ("融券余额", 1e8, "securities"),
    "TOTAL": ("融资融券余额", 1e8, "total"),
    "BUYING": ("融资买入额", 1e8, "buying"),
}
ACCT_META = {
    "MAINTENANCE": ("平均维持担保比例", 1.0, "maintenance"),
    "COLLATERAL": ("担保物总价值", 1.0, "collateral"),
    "INVESTORS": ("参与交易的投资者数量", 1e4, "investors"),
}


def _today() -> dt.date:
    """校验节流的"今天"（独立函数便于测试注入确定日期）。"""
    return dt.date.today()


@register("akshare.margin")
class MarginFetcher:
    source_id = "margin"
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        self._lg = {"SH": {}, "SZ": {}}   # 后缀 → iid（列名/除数/metric 查 LG_META）
        self._acct = {}                    # 后缀 → iid
        for i in instruments:
            code = i["code"] or ""
            parts = code.split("-")        # MARGIN-{SH|SZ}-{后缀} / MARGIN-{后缀}
            if len(parts) == 3 and parts[0] == "MARGIN" and parts[1] in ("SH", "SZ") \
                    and parts[2] in LG_META:
                self._lg[parts[1]][parts[2]] = i["id"]
            elif len(parts) == 2 and parts[0] == "MARGIN" and parts[1] in ACCT_META:
                self._acct[parts[1]] = i["id"]
            else:
                raise ValueError(
                    f"margin 未支持的仪器代码: {code}（约定 MARGIN-{{SH|SZ}}-"
                    f"{{FINANCING|SECURITIES|TOTAL|BUYING}} 或 MARGIN-"
                    f"{{MAINTENANCE|COLLATERAL|INVESTORS}}）")

    def fetch(self, trade_date: str) -> list[Observation]:
        branches = [
            ("lg_sh", partial(self._fetch_lg, "SH", self._lg["SH"])),
            ("lg_sz", partial(self._fetch_lg, "SZ", self._lg["SZ"])),
            ("account", partial(self._fetch_account, self._acct)),
        ]
        obs = run_branches(log, "margin", branches)
        self._cross_check(trade_date, obs)
        return obs

    def _fetch_lg(self, exchange: str, targets: dict) -> list[Observation]:
        if not targets:
            return []
        df = getattr(ak, f"macro_china_market_margin_{exchange.lower()}")()
        return self._wide_to_obs(df, targets, LG_META)

    def _fetch_account(self, targets: dict) -> list[Observation]:
        if not targets:
            return []
        return self._wide_to_obs(ak.stock_margin_account_info(), targets, ACCT_META)

    @staticmethod
    def _wide_to_obs(df, targets: dict, meta: dict) -> list[Observation]:
        """全历史宽表 → 观测：日期列归一、逐行逐仪器取值除换算、NaN 跳行。"""
        if df is None or df.empty:
            return []
        c_d = col(df, ["日期"], "日期")
        out = []
        for _, r in df.iterrows():
            d = normalize_date(r[c_d])
            if d is None:
                continue
            for sfx, iid in targets.items():
                cname, div, metric = meta[sfx]
                v = r.get(cname)
                if v is not None and not pd.isna(v):
                    out.append(Observation(iid, metric, d, float(v) / div))
        return out

    def _cross_check(self, trade_date: str, obs: list[Observation]) -> None:
        """交叉校验占位（Task 5 实现，本任务空转保证 fetch 契约完整）。"""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync python -m pytest backend/tests/test_fetcher_margin.py -q`
Expected: 6 个测试全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/fetchers/akshare_margin.py backend/tests/test_fetcher_margin.py
git commit -m "feat: margin fetcher 骨架+乐咕主通道（沪深8仪器，元→亿元）"
```

---

### Task 4: 账户统计通道（全市场 3 仪器 + NaN 跳行）

**Files:**
- Modify: `backend/tests/test_fetcher_margin.py`（追加测试）

- [ ] **Step 1: 写失败测试**

```python
def test_account_units(margin_env):
    # 账户统计：担保比例 %/担保物 亿元原样、投资者数 个 → ÷1e4 万人
    obs = margin_env.fetch(FIX_LATEST)
    by = {(o.instrument_id, o.trade_date): o.value for o in obs}
    (main,) = [v for (iid, d), v in by.items()
               if iid == "margin-maintenance" and d == FIX_LATEST]
    assert 130 < main < 400                     # % 量级（2026-10 实测 ≈256）
    (inv,) = [v for (iid, d), v in by.items()
              if iid == "margin-investors" and d == FIX_LATEST]
    assert 10 < inv < 100                       # 万人（29.65 万实测量级）
    (col_v,) = [v for (iid, d), v in by.items()
                if iid == "margin-collateral" and d == FIX_LATEST]
    assert 50000 < col_v < 100000               # 亿元（7.4 万亿 ≈ 74000 亿实测量级）
    metrics = {o.metric for o in obs if o.instrument_id.startswith("margin-")
               and not o.instrument_id[7:9] in ("sh", "sz")}
    assert {"maintenance", "collateral", "investors"} <= metrics


def test_account_early_nan_skipped(margin_env):
    # 2012-09-27 首行：担保物/投资者数 NaN → 只产出 maintenance，不产 nan/崩
    obs = margin_env.fetch(FIX_LATEST)
    first = [o for o in obs if o.trade_date == "2012-09-27"]
    iids = {o.instrument_id for o in first}
    assert iids == {"margin-maintenance"}
    assert all(o.value == o.value for o in first)   # 无 NaN 混入
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run --no-sync python -m pytest backend/tests/test_fetcher_margin.py -q -k "account"`
Expected: 2 个测试 FAIL（观测缺失或量级不符——骨架里 _fetch_account 已在 branches 但 ACCT_META 换算已实现则可能直接过；若直接过，验证实现正确性并进 Step 3 补交叉校验前确认这两测通过即跳到 Task 5）。

- [ ] **Step 3: 修复实现（如 Step 2 失败）**

Task 3 骨架已含账户通道（ACCT_META + `_fetch_account`）。若 Step 2 失败，对照实测契约排查：列名拼写、÷1e4 换算、NaN 判断（`v is not None and not pd.isna(v)`）。

- [ ] **Step 4: 运行全文件确认通过**

Run: `uv run --no-sync python -m pytest backend/tests/test_fetcher_margin.py -q`
Expected: 8 个测试全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_fetcher_margin.py
git commit -m "test: margin 账户统计通道（万人换算+早期 NaN 跳行锁定）"
```

---

### Task 5: 交叉校验通道（SSE 区间 + SZSE 单日 + 节流三态）

**Files:**
- Modify: `backend/fetchers/akshare_margin.py`（实现 `_cross_check` 及子方法，替换占位）
- Modify: `backend/tests/test_fetcher_margin.py`（追加校验测试）

- [ ] **Step 1: 写失败测试**

```python
# ---------- 交叉校验通道（Task 5） ----------
# SSE 直连真实样本（2026-10-21 实测列与值；信用交易日期为 YYYYMMDD str）
def _sse_df(date_str, financing, total):
    d8 = date_str.replace("-", "")
    return pd.DataFrame([{"信用交易日期": d8, "融资余额": financing,
                          "融资买入额": financing * 0.05, "融券余量": 3.2e9,
                          "融券余量金额": 1.9e10, "融券卖出量": 6.9e7,
                          "融资融券余额": total}])


def test_cross_check_warns_on_mismatch(margin_env, monkeypatch, caplog):
    # SSE 直连值偏离乐咕 2%（>0.5% 容差）→ warning 告警
    import backend.fetchers.akshare_margin as mod
    sh_fin = pd.read_json(FIX / "margin_lg_sh.json").iloc[-1]["融资余额"]
    monkeypatch.setattr(
        mod.ak, "stock_margin_sse",
        lambda start_date, end_date: _sse_df(FIX_LATEST, sh_fin * 1.02, 1.3e12))
    margin_env.fetch(FIX_LATEST)
    assert "不一致" in caplog.text and "financing" in caplog.text


def test_cross_check_within_tolerance(margin_env, monkeypatch, caplog):
    # 差 0.1%（容差内）→ 无 warning
    import backend.fetchers.akshare_margin as mod
    sh_fin = pd.read_json(FIX / "margin_lg_sh.json").iloc[-1]["融资余额"]
    monkeypatch.setattr(
        mod.ak, "stock_margin_sse",
        lambda start_date, end_date: _sse_df(FIX_LATEST, sh_fin * 1.001, 1.3e12))
    margin_env.fetch(FIX_LATEST)
    assert "不一致" not in caplog.text


def test_cross_check_silent_on_direct_failure(margin_env, monkeypatch, caplog):
    # 直连断连（env 默认 _raise）→ info 静默，产出不受影响、无 warning
    obs = margin_env.fetch(FIX_LATEST)
    assert obs and "不一致" not in caplog.text and "SSE net down" in caplog.text


def test_cross_check_skipped_for_old_dates(margin_env, monkeypatch):
    # 远古目标日（>30 日历日）→ 零直连调用（backfill 2010 起不逐日校验）
    import backend.fetchers.akshare_margin as mod
    calls = []

    def probe(start_date, end_date):
        calls.append(start_date)
        return _sse_df(FIX_LATEST, 1.0, 1.0)

    monkeypatch.setattr(mod.ak, "stock_margin_sse", probe)
    margin_env.fetch("2020-01-02")
    assert calls == []


def test_cross_check_structure_change_warns(margin_env, monkeypatch, caplog):
    # SSE 列结构变更（缺日期列 col() ValueError）→ warning 而非静默
    # （校验通道结构变更必须可见，否则永久失去校验能力无人知）
    import backend.fetchers.akshare_margin as mod
    monkeypatch.setattr(mod.ak, "stock_margin_sse",
                        lambda start_date, end_date: pd.DataFrame([{"x": 1}]))
    margin_env.fetch(FIX_LATEST)
    assert "结构变更" in caplog.text
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run --no-sync python -m pytest backend/tests/test_fetcher_margin.py -q -k "cross_check"`
Expected: 5 个测试 FAIL（`_cross_check` 是占位空转，warning/调用探针均未发生）。

- [ ] **Step 3: 实现 `_cross_check`（替换占位方法）**

```python
    # 交易所直连列映射（单位元，与乐咕原值同单位可比；SSE"融券余量金额"即融券余额）
    SSE_COLS = {"financing": "融资余额", "securities": "融券余量金额",
                "total": "融资融券余额", "buying": "融资买入额"}
    SZ_COLS = {"financing": "融资余额", "securities": "融券余额",
               "total": "融资融券余额", "buying": "融资买入额"}
    CHECK_TOLERANCE = 0.005
    CHECK_RECENT_DAYS = 30

    def _cross_check(self, trade_date: str, obs: list[Observation]) -> None:
        """交易所直连交叉校验（只告警不落库）：近期目标日尽力比对。
           远古目标日静默跳过（backfill 2010 起逐日校验需数千请求，不划算——
           交易所与乐咕同源转发，近期段持续校验已覆盖设计意图）。"""
        if (_today() - dt.date.fromisoformat(trade_date)).days > self.CHECK_RECENT_DAYS:
            return
        # 只取目标日当天的沪深产出（fetch 全历史返回，远古行不参与）
        mine = {}
        for o in obs:
            if o.trade_date == trade_date:
                parts = o.instrument_id.split("-")   # margin-sh-financing
                if len(parts) == 3 and parts[1] in ("sh", "sz"):
                    mine[(parts[1].upper(), o.metric)] = o.value
        self._check_sse(trade_date, mine)
        self._check_szse(trade_date, mine)

    def _check_sse(self, trade_date: str, mine: dict) -> None:
        try:
            start = (dt.date.fromisoformat(trade_date)
                     - dt.timedelta(days=self.CHECK_RECENT_DAYS)).strftime("%Y%m%d")
            df = ak.stock_margin_sse(start_date=start,
                                     end_date=trade_date.replace("-", ""))
            row = None
            if df is not None and not df.empty:
                c_d = col(df, ["信用交易日期"], "日期")
                row = next((r for _, r in df.iterrows()
                            if normalize_date(r[c_d]) == trade_date), None)
        except ValueError as e:   # 列结构变更必须可见（否则永久失去校验能力无人知）
            log.warning("两融校验通道结构变更（SSE）: %s", e)
            return
        except Exception as e:    # 直连断连/限流——校验是辅助功能，静默跳过
            log.info("两融校验跳过（SSE 直连失败）: %s", e)
            return
        if row is None:
            log.info("两融校验跳过（SSE 尚无 %s 数据）", trade_date)
            return
        self._compare("SH", self.SSE_COLS, row, mine, trade_date)

    def _check_szse(self, trade_date: str, mine: dict) -> None:
        try:
            df = ak.stock_margin_szse(date=trade_date.replace("-", ""))
            row = None if df is None or df.empty else df.iloc[0]
        except ValueError as e:
            log.warning("两融校验通道结构变更（SZSE）: %s", e)
            return
        except Exception as e:
            log.info("两融校验跳过（SZSE 直连失败）: %s", e)
            return
        if row is None:
            log.info("两融校验跳过（SZSE 尚无 %s 数据）", trade_date)
            return
        self._compare("SZ", self.SZ_COLS, row, mine, trade_date)

    def _compare(self, exch: str, cols: dict, row, mine: dict,
                 trade_date: str) -> None:
        for metric, cname in cols.items():
            base = mine.get((exch, metric))
            theirs = row.get(cname) if hasattr(row, "get") else None
            if base is None or theirs is None or pd.isna(theirs):
                continue
            if abs(float(theirs) - base) / base > self.CHECK_TOLERANCE:
                log.warning("两融交叉校验不一致: %s %s %s 乐咕=%.2f 交易所=%.2f",
                            exch, trade_date, metric, base, float(theirs))
```

同时删除占位方法 `_cross_check`（原 docstring 体），换成上面实现。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --no-sync python -m pytest backend/tests/test_fetcher_margin.py -q`
Expected: 13 个测试全部 PASS。

- [ ] **Step 5: 全量回归**

Run: `uv run --no-sync python -m pytest -q`
Expected: 全部 PASS（此前基线 227 + margin 新增 13 + db 1 ≈ 241）。

- [ ] **Step 6: Commit**

```bash
git add backend/fetchers/akshare_margin.py backend/tests/test_fetcher_margin.py
git commit -m "feat: margin 交叉校验通道（SSE/SZSE 直连，容差0.5%+远古节流三态）"
```

---

### Task 6: 前端分区（5 区 + 多 metric 区支持）

**Files:**
- Modify: `frontend/src/metricSections.js`
- Modify: `frontend/src/metricSections.test.js`（追加）

- [ ] **Step 1: 写失败测试**

`frontend/src/metricSections.test.js` 末尾追加：

```js
// ---------- v1.4 融资融券分区（设计 §5.4） ----------
const MARGIN_META = {
  "margin-sh-financing": { metric: "financing", unit: "亿元" },
  "margin-sz-financing": { metric: "financing", unit: "亿元" },
  "margin-sh-total": { metric: "total", unit: "亿元" },
  "margin-sz-total": { metric: "total", unit: "亿元" },
  "margin-maintenance": { metric: "maintenance", unit: "%" },
  "margin-collateral": { metric: "collateral", unit: "亿元" },
  "margin-investors": { metric: "investors", unit: "万人" },
};

describe("v1.4 margin 分区", () => {
  test("识别为分区页（非 share 源）", () => {
    expect(isCommodityMeta(MARGIN_META)).toBe(true);
  });

  test("5 分区顺序：融资/融券/两融/买入/杠杆状况", () => {
    const secs = groupSections(MARGIN_META);
    expect(secs.map(s => s.key)).toEqual(
      ["financing", "securities", "total", "buying", "leverage"]);
  });

  test("杠杆状况区收 3 个 metric（keys 多值）", () => {
    const lev = groupSections(MARGIN_META).find(s => s.key === "leverage");
    expect(lev.iids).toEqual(["margin-maintenance", "margin-collateral", "margin-investors"]);
  });

  test("杠杆状况区内按单位分 3 图（%/亿元/万人 不同图）", () => {
    const lev = groupSections(MARGIN_META).find(s => s.key === "leverage");
    const groups = groupCharts(lev.iids, MARGIN_META);
    expect(groups.map(g => g.unit)).toEqual(["%", "亿元", "万人"]);
  });

  test("余额区沪+深同图（同为亿元不拆）", () => {
    const fin = groupSections(MARGIN_META).find(s => s.key === "financing");
    const groups = groupCharts(fin.iids, MARGIN_META);
    expect(groups).toHaveLength(1);
    expect(groups[0].iids).toEqual(["margin-sh-financing", "margin-sz-financing"]);
  });

  test("既有商品源分区不回归（leverage 等 margin metric 不混入商品源页面）", () => {
    const goldMeta = { "au-shfe-price": { metric: "price", unit: "元/克" } };
    expect(groupSections(goldMeta).map(s => s.key)).toEqual(["price"]);
  });
});
```

⚠️ 先读现有 `metricSections.test.js` 的 import 与 describe 风格（ESM import 具名导入），保持一致；若文件是 `import { groupSections, groupCharts, isCommodityMeta } from "./metricSections.js"` 则上述测试直接追加到文件尾部。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/metricSections.test.js`
Expected: 新增 6 测试中 4 个 FAIL（分区 key 不存在/keys 未支持）、2 个 PASS（isCommodityMeta 天然成立、gold 不回归）。

- [ ] **Step 3: 实现（metricSections.js）**

`METRIC_SECTIONS` 数组尾部追加 5 项：

```js
  // ---- v1.4 融资融券（设计 §5.4）：余额类沪/深两线一图，杠杆状况区 3 metric 按单位分 3 图 ----
  { key: "financing", label: "融资余额" },
  { key: "securities", label: "融券余额" },
  { key: "total", label: "两融余额", note: "融资余额+融券余额，沪深交易所口径；全市场口径见杠杆状况区" },
  { key: "buying", label: "融资买入额" },
  { key: "leverage", label: "杠杆状况",
    keys: ["maintenance", "collateral", "investors"],  // 多 metric 同区（keys 数组）
    note: "全市场两融账户口径（含北交所）；担保比例预警线 130%，担保物与投资者数 2015 年前后起有值" },
```

`groupSections` 支持多 metric 区（向后兼容：无 keys 视为 [key]）：

```js
export function groupSections(meta) {
  return METRIC_SECTIONS
    .map(s => {
      const keys = s.keys ?? [s.key];   // 多 metric 区（如 leverage）声明 keys 数组
      return { ...s, keys,
               iids: Object.keys(meta ?? {}).filter(iid => keys.includes(meta[iid].metric)) };
    })
    .filter(s => s.iids.length);
}
```

- [ ] **Step 4: 运行前端测试确认通过**

Run: `cd frontend && npx vitest run`
Expected: 全部 PASS（既有测试零回归——`keys ?? [s.key]` 对单 metric 区行为不变）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/metricSections.js frontend/src/metricSections.test.js
git commit -m "feat: margin 详情页 5 分区（杠杆状况区多 metric 按单位分图）"
```

---

### Task 7: 回补历史 + 部署 + 端到端验证

**Files:** 无代码改动（运维步骤）

- [ ] **Step 1: 全量测试 + 前端构建确认**

Run: `uv run --no-sync python -m pytest -q && cd frontend && npm run build`
Expected: pytest 全 PASS；vite build 成功。

- [ ] **Step 2: 重建镜像并重启**

```bash
docker compose --profile tools build 2>&1 | tail -3
docker compose up -d 2>&1 | tail -2
docker compose logs scheduler --since 1m 2>&1 | grep 下次采集
```
Expected: 三镜像 Built；scheduler 输出下次采集时间。

- [ ] **Step 3: 回补全历史（3 请求）**

```bash
docker compose run --rm collect uv run --no-sync python -m backend.collect \
  --backfill --from 2010-01-01 --source margin
```
Expected: 完成、failed=0；乐咕两接口+账户统计各 1 次全历史拉取（远古目标日 covered 门控，不逐日请求；近 30 日内目标日触发 SSE/SZSE 校验——SZSE 断连静默属预期）。

- [ ] **Step 4: 落库验证**

```bash
docker compose run --rm --no-deps collect uv run --no-sync python -c "
import sqlite3
c = sqlite3.connect('/app/data/dtp.db')
print('margin 观测总数:', c.execute(
    \"SELECT COUNT(*) FROM observations o JOIN instruments i ON i.id=o.instrument_id\"
    \" WHERE i.source_id='margin'\").fetchone()[0])
for iid in ('margin-sh-financing','margin-sz-total','margin-maintenance'):
    r = c.execute('SELECT MIN(trade_date) a, MAX(trade_date) b FROM observations'
                  ' WHERE instrument_id=?', (iid,)).fetchone()
    print(iid, r['a'], '→', r['b'])
print('最新沪融资余额(亿):', c.execute(
    \"SELECT ROUND(value,1) FROM observations WHERE instrument_id='margin-sh-financing'\"
    \" ORDER BY trade_date DESC LIMIT 1\").fetchone()[0])
"
```
Expected: 观测总数 ≈ 4015×8 + 3410×3 − NaN 行 ≈ 42000；financing 2010-03 → 最新；maintenance 2012-09 → 最新；最新沪融资余额 ≈ 12580 亿。

- [ ] **Step 5: API 与页面验证**

```bash
curl -s 'http://localhost:8000/api/sources/margin' | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['name'], len(d['cards']), 'cards, as_of', d.get('as_of_date'))"
```
Expected: `融资融券 11 cards, as_of 2026-10-2x`。浏览器打开 `http://localhost:8000/source/margin`：5 分区、融资余额图沪/深两线、杠杆状况区 3 图（%/亿元/万人）。

- [ ] **Step 6: Commit（如有文档更新）+ 汇报**

无代码改动则跳过 commit；向用户汇报回补结果与页面入口。

---

## Self-Review 记录

- **Spec 覆盖**：§5.4 仪器表（Task 2 seed 11 仪器 ✓）、乐咕/账户/SSE/SZSE 四通道（Task 3/4/5 ✓）、容差 0.005+节流 30 日历日（Task 5 ✓，设计"15 交易日"以 30 日历日近似——含节假日缓冲，实现更简单）、lag=1/movement_th=2.0 环比%（Task 2 ✓，v1.4 审查修正）、前端 5 分区+杠杆状况 3 图（Task 6 ✓）、fixture（Task 1 ✓）、回补 --from 2010-01-01（Task 7 ✓）
- **占位符**：Task 3 Step 3 占位 `_cross_check` 为渐进实现脚手架，Task 5 Step 3 完整替换——非 TBD
- **类型一致**：`_wide_to_obs(df, targets: dict, meta: dict)`、`_cross_check(trade_date, obs)`、`_today()` 注入点在 Task 3 定义、Task 5 使用一致；`LG_META/ACCT_META` 键结构 (cname, div, metric) 全文一致
