# 数据追踪平台 · 设计文档

版本：v1.1 ｜ 日期：2026-10-16 ｜ 状态：**已实现，端到端验收通过**（v1.0 设计 → 实现 → 三阶段审查 → 验收）
产品需求见 [docs/PRD.md](../../PRD.md)

---

## 1. 概述

内网单机部署的数据追踪平台：每日收盘后从公开数据源自动采集（Python/akshare），存入 SQLite(WAL)，FastAPI 提供只读 API 并托管 Vue3 + ECharts 前端。核心设计约束（来自用户）：**第一版界面按参考图实现，架构必须为后续新数据源、新功能预留适配性**——落地方式为"数据源注册表驱动一切"。当前已接入 7 个数据源（5 类指数 ETF + 电解铜库存；煤炭价格顺延），历史深度达 11 年（2014-12-31 起）。

## 2. 总体架构

```
┌────────────────────────────────────────────────────────────┐
│ 内网 Linux VM（宿主机仅需 Docker）                            │
│                                                            │
│  docker compose up -d ──► 三个服务同镜像：                     │
│                                                            │
│  ┌─ scheduler（常驻调度器 backend/scheduler.py）             │
│  │   交易日 16:40 触发采集（日历感知，节假日/周末自动跳过）      │
│  │   启动/例行后自动补跑漏掉的交易日（自愈，替代 systemd Persistent）│
│  │   跨年日历耗尽防假死（上界降级 + 400 次循环上限）             │
│  │        │                                                 │
│  │        ▼ 进程内调用                                      │
│  ├─ collect（按需，--profile tools；跑完即退）                │
│  │   遍历 Fetcher 注册表 → 逐源独立执行                       │
│  │   tenacity 重试 → 幂等 upsert → 校验 → job_runs → webhook │
│  │        │                                                 │
│  │        ▼                                                 │
│  ├─ SQLite 单文件（WAL 模式，宿主机卷 ./data）                 │
│  │        ▲ 只读连接（每请求一连接）                           │
│  └─ web（常驻，端口 8000）                                    │
│      uvicorn: FastAPI                                       │
│      ├─ /api/* 只读 JSON API                                │
│      └─ StaticFiles 托管 frontend/dist（SPA fallback）       │
│           ▲                                                 │
│  团队浏览器 http://<内网IP>:8000                              │
│  Vue3 + Vite + vue-echarts（ECharts 6）                     │
└────────────────────────────────────────────────────────────┘
```

要点：

- **运行环境整体容器化（用户约束，含调度）**：前后端构建产物与调度器打进同一 Docker 镜像，`docker compose up -d` 一条命令全起；宿主机只需安装 Docker，Python/Node 环境完全隔离，**零 sudo 运维**
- 除 web/scheduler 两个常驻容器外零常驻进程；采集器与 Web 服务通过 SQLite 解耦（WAL 下读不阻塞写）
- 单源失败不阻断：逐源 try/except，失败的源标记 `failed`，看板其他部分照常
- 告警双通道：脚本收尾统一推送汇总（成功行数/失败原因/新鲜度）到 webhook（企业微信/钉钉 text 格式，errcode 级失败检测 + 长度上限）；调度器漏跑由启动补跑自愈

## 3. 技术栈

| 层 | 选型 | 版本/说明 |
|----|------|-----------|
| 语言/包管理 | Python + uv | Python 3.12，`uv sync` 一键装依赖 |
| 数据接口 | akshare | `>=1.19.1`（修复广期所仓单接口）+ tenacity 重试 |
| 存储 | SQLite (WAL) | `journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000` |
| Web | FastAPI + uvicorn | 只读 API，StaticFiles 托管前端构建产物 |
| 前端 | Vue3 + Vite + vue-echarts | ECharts 6（dataZoom/tooltip/图例开关/双Y轴原生支持） |
| 样式 | 纯 CSS | 深色侧边栏贴参考图，不引 UI 框架 |
| 环境隔离 | Docker + docker compose | 多阶段构建：`node:20-slim` 编译前端 → `python:3.12-slim` + uv 运行时；非 root 运行（user 1000），uv 缓存构建/运行期分离；宿主机仅装 Docker |
| 调度 | 容器内置 Python 调度器（默认） | 交易日历感知 16:40 + 启动/例行后补跑 + 跨年防假死；`deploy/systemd/` 单元保留为宿主机备选 |

明确排除项及理由（调研/实施结论）：Grafana（无法实现参考图的定制布局/导航树，SQLite 插件限制多）、Streamlit（样式受组件库约束、多页交互弱）、Evidence.dev（动态对比交互弱）、DuckDB 主存储（读写进程互斥，与常驻 Web 连接冲突）、APScheduler/Prefect/Airflow（自写 ~140 行调度循环已覆盖日历感知/补跑/防假死，编排平台属过重）、宿主机 systemd timer（已改为容器内置调度器，systemd 单元降级为备选——用户要求 `docker compose up -d` 零宿主机运维）。

## 4. 数据模型

```sql
-- 数据源注册表：驱动侧边栏、详情页、异动扫描
CREATE TABLE sources (
  id            TEXT PRIMARY KEY,        -- 'hs300-etf'
  name          TEXT NOT NULL,           -- '沪深300 ETF'
  group_name    TEXT NOT NULL,           -- '指数 ETF' | '大宗商品'
  freq          TEXT NOT NULL,           -- 'daily' | 'weekly' | 'monthly'
  fetcher       TEXT NOT NULL,           -- 'akshare.sse_etf_scale'（注册表键）
  unit          TEXT,                    -- '亿份' | '元/吨' | '吨'
  movement_th   REAL,                    -- 异动阈值（环比 %，如 1.0）
  lag_days      INTEGER DEFAULT 0,       -- 数据发布滞后（T+1 源=1）；采集目标日=run_date 之前第 lag_days 个交易日（非日历日）
  enabled       INTEGER DEFAULT 1,
  sort_order    INTEGER
);

-- 成分品种：属于某数据源的可观测个体
CREATE TABLE instruments (
  id          TEXT PRIMARY KEY,          -- '510300'
  source_id   TEXT REFERENCES sources(id),
  code        TEXT,                      -- '510300' / NULL（单值源如煤炭价格）
  metric      TEXT NOT NULL,             -- 'share' | 'price' | 'inventory'
  name        TEXT NOT NULL,             -- '沪深300ETF华泰柏瑞'
  sort_order  INTEGER
);

-- 日度观测：唯一键保证幂等
CREATE TABLE observations (
  instrument_id TEXT NOT NULL REFERENCES instruments(id),
  metric        TEXT NOT NULL,           -- 'share' | 'price' | 'inventory'
  trade_date    TEXT NOT NULL,           -- 'YYYY-MM-DD'
  value         REAL,
  raw_json      TEXT,                    -- 原始返回留档，防口径变化
  fetched_at    TEXT NOT NULL,
  UNIQUE(instrument_id, metric, trade_date)   -- UNIQUE 自动索引即序列查询索引，无需另建
);

-- 抓取状态：驱动总览页状态条与告警
CREATE TABLE job_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id   TEXT,
  trade_date  TEXT,
  status      TEXT NOT NULL,             -- 'ok' | 'failed' | 'empty' | 'stale'
  rows        INTEGER,
  error       TEXT,
  started_at  TEXT, finished_at TEXT
);
```

查询层全用 SQL 窗口函数：趋势 = 按日期排序取序列；环比异动 = `value / LAG(value) - 1`；归一化 = `value / FIRST_VALUE(value) OVER (PARTITION BY ... ORDER BY trade_date) * 100`。

## 5. 数据源注册表与 Fetcher 设计

```
backend/fetchers/
├── base.py              # Fetcher 协议 + 注册表（@register 重名 fail-loud）
├── _util.py             # 公共工具：col/norm_code/clean_name/normalize_date + 列名候选常量
├── akshare_sse_etf.py   # 上交所 ETF 份额（fund_etf_scale_sse，快照过滤）
├── akshare_etf_scale.py # 混合交易所 ETF 份额（按代码前缀分流：5x/6x→上交所，15x→深交所单日）
├── akshare_cu_inventory.py  # 电解铜库存（东财 60 日窗口全量 upsert）
└── scrapers/            # 自研轻爬虫（SCFI、动力煤，待实现）
```

Fetcher 协议（每个数据源实现一个）：

```python
class Fetcher(Protocol):
    source_id: str
    lag_days: int            # 数据发布滞后天数（如上交所份额 T+1 → 1）
    def __init__(self, source, instruments): ...   # 3.12 必需存根（gh-92445）
    def fetch(self, trade_date: str) -> list[Observation]:
        """返回该源当日全部观测；失败抛异常，由执行器捕获"""
```

注册表由 `sources.fetcher` 字符串解析加载 → **新增数据源不改执行器**。三个 akshare fetcher 共享 `_util.py`（三次法则抽取，SHARE_CANDIDATES 等候选常量共享防漂移）。

### 5.1 数据源清单（2026-10-16 现状：7 源接入，1 源顺延）

| source | fetcher | 接口 | 状态 / 备注 |
|--------|---------|------|------------|
| 沪深300 ETF 份额（5 只） | `akshare.sse_etf_scale` | `fund_etf_scale_sse(date)` | ✅ 已回补 11 年（2014-12-31 起 2862 个交易日）；T+1 |
| 中证500 ETF 份额（嘉实159922/南方512500/天弘159820） | `akshare.etf_scale` | 上交所快照 + `fund_scale_daily_szse(start,end)` 深交所单日 | ✅ 沪深混合，自 2015-05 |
| 科创50 ETF 份额（华夏588000/易方达588080/科创50E588020） | `akshare.etf_scale` | 同上 | ✅ 自 2020-11 上市起 |
| 创业板 ETF 份额（易方达159915/华安创50 159949/广发159952） | `akshare.etf_scale` | 同上（全深交所） | ✅ 自 2016-10 |
| 红利 ETF 份额（红利低波512890/红利50 515450/中证红利515180） | `akshare.etf_scale` | 同上 | ✅ 自 2019-01 |
| 电解铜库存 | `akshare.cu_inventory` | `futures_inventory_em('cu')` | ✅ 窗口型：仅保留近 60 交易日，每次全量 upsert 自建累积；backfill 窗口型早停（rows>0 守卫） |
| 煤炭价格 | `scrapers.coal_price` | 无可用接口 | ⏸ 顺延：动力煤轻爬虫（CCTD/生意社），**不用焦煤 JM 等近似品种替代**（口径会误导投研）；落地前状态条如实显示 failed，每日备份门控（全部源成功）暂不触发 |

批次剩余（按 PRD 5.2）：两融 `macro_china_market_margin_sh/sz` + `stock_margin_sse/szse`；商品价格 `futures_spot_price`；上期所/广期所仓单；黄金储备 `macro_china_foreign_exchange_gold`；SCFI 轻爬虫；水文（最难，最后）。新源选样原则：**每类取规模最大的几只 ETF 反映市场情况**（两所接口实测份额排序）。

## 6. 采集管道

`backend/collect.py` 执行流程：

1. **交易日判断**：`tool_trade_date_hist_sina` 取交易日历（进程级 lru_cache），非交易日 exit 0；日历失败降级工作日 + warning
2. **逐源执行**：遍历 `sources WHERE enabled=1`；每源独立 try/except + tenacity 重试 3 次（指数退避）；单源异常记录 `job_runs.status='failed'` 继续
3. **写入**：`ON CONFLICT(instrument_id, metric, trade_date) DO UPDATE`（唯一键幂等，重跑/补跑安全）；窗口型 fetcher（库存）每次返回完整近 60 日区间全部 upsert
4. **校验**（防静默失败）：本次 0 行 → `empty`；采集目标日（=前一交易日，非日历日）> 库内最新 → `stale`
5. **汇总**：写 job_runs；推 webhook 汇总（errcode 级失败检测、1800 字节上限）；无 webhook 配置时仅落库与日志
6. **备份**：全部成功后 Python `conn.backup()` 单文件快照（镜像内无 sqlite3 CLI），滚动保留 30 天；备份失败仅告警不改退出码（防误触发崩溃告警）
7. **退出码**：仅 `failed` 非零；`empty`/`stale` 属数据质量事件走 job_runs + webhook（systemd OnFailure 备选场景下不误告警）

**历史回补**：`collect.py --backfill --from 2015-01-01 [--source hs300-etf]`
- **按目标日判覆盖**（逐日检查该源目标日是否已有数据，精确跳过）——支持在已有新数据时向前补历史、断点续跑（失败即 break，重跑续传）
- **窗口型早停**：fetch 返回非空行但不覆盖目标日（rows>0 守卫区分"上市前空日"）→ 判定窗口型，停止向更早回补
- **历史深度原则**：数据源能提供多深就回补多深（上交所份额 2015 年起 ≈11 年）；短窗口源靠每日采集持续累积，以 5-10 年为目标

**容器内置调度器（默认，`backend/scheduler.py`）**：常驻容器，交易日 16:40（Asia/Shanghai）触发 `C.main([])`；启动与例行后自动补跑漏掉的交易日（对 max < 昨日交易日的源按 `--backfill --from max+1` 追平，幂等零开销）；跨年日历耗尽防假死（日历上界外按工作日降级 + 400 次循环上限 fail-safe）。

**容器化与部署（docker compose）**：

- **单一镜像、多阶段构建**：阶段 1 `node:20-slim` 执行 `npm ci && npm run build` 产出 `dist/`；阶段 2 `python:3.12-slim` + uv（固定 0.13.1）安装后端依赖并拷入 `dist/`
- **compose 服务**：`web`（常驻 API）、`scheduler`（常驻调度器）、`collect`（`--profile tools` 按需——⚠️ 裸 `docker compose build` 会跳过 profile 服务，重建须 `docker compose --profile tools build`）；全部 `user: "1000:1000"`、挂载 `./data:/app/data`、`TZ=Asia/Shanghai`
- uv 缓存构建期（`/tmp/uv-build-cache`）与运行期（`/tmp/uv-cache`）分离——root 构建写入的缓存目录会卡死非 root 运行
- SQLite WAL 运行于本地 bind mount（满足"非 NFS"约束）；数据库与备份（`./data/backups`）均在宿主机卷上，容器可随时重建
- 备选：`deploy/systemd/` 宿主机 systemd 单元（OnFailure 崩溃告警等），README 注明

## 7. API 设计

| Method | Path | 用途 |
|--------|------|------|
| GET | `/api/sources` | 侧边栏 + 总览速览卡：分组数据源列表 + inst_count/latest_date/**total_value/day_change**（latest CTE 求各成分最新行合计与日变化） |
| GET | `/api/sources/{id}?range=3m` | 详情页数据：成分卡片（最新值/日变化/区间变化，WINDOW 函数计算）+ 各成分序列 |
| GET | `/api/series?instruments=a,b&range=6m&normalize=1` | 跨源取序列：入参支持 instrument id 与 **source id（自动展开为该源全部成分）**；normalize=1 期初基线 100；点列确定性升序（ORDER BY instrument_id, trade_date） |
| GET | `/api/movements?date=` | 今日异动榜：跨源环比超过 `movement_th` 按 \|pct\| 降序，含 5 日 sparkline 与各源 trade_date（源更新滞后由前端标注） |
| GET | `/api/jobs/latest` | 抓取状态条：各源最近一次 job_runs |
| GET | `/api/meta/trading-dates` | 可选时间范围（v1 前端用固定档位切换，此端点延后实现） |

约定：所有序列响应携带 `as_of_date`（数据实际日期）；**SQL 一律绑参**（as_of 等源自上游外部数据的值严禁 f-string 拼接）；API 只读，无鉴权（内网）；未知 `/api/*` 路径返回 404（防 SPA fallback 返回 HTML 200 造成前端静默失败）。

## 8. 前端设计

```
frontend/src/
├── views/
│   ├── OverviewView.vue      # 总览：异动榜（异动优先布局）+ 分组速览 + JobStatus + 加载/错误三态守卫
│   └── SourceDetailView.vue  # 详情：banner + RANGES 时间条 + CardRow + CompareChips + TrendChart
│                             #      seq/cseq 双竞态守卫；切源清空对比上下文
├── components/
│   ├── MovementsTable.vue    # 异动榜（红涨绿跌、tabular-nums、5日sparkline、陈旧源日期标注、行下钻）
│   ├── SourceTiles.vue       # 速览卡（名称/合计值/日变化，hasChange 防除零）
│   ├── CardRow.vue           # 成分卡片行
│   ├── TrendChart.vue        # vue-echarts 封装：多线/图例开关/dataZoom/containLabel/percent 轴
│   ├── CompareChips.vue      # 对比 chips（source 粒度）+ 量纲自动归一（跨单位 emit added→normalize=1）
│   └── JobStatus.vue         # 抓取状态（红底对比度覆写，总览/详情复用）
├── api.js                    # fetch 封装（错误带 URL）
└── format.js                 # fmtVal/fmtChange/pctColor/sparkBars（Vitest 单测；红涨绿跌常量与 App.vue 同步）
```

路由：`/`（总览）、`/source/:id`（详情）。渲染完全由 API 数据驱动：侧边栏分组、卡片字段、曲线数量均来自 `sources`/`instruments` 配置——**新增数据源前端零改动**（四类 ETF 实战验证）。归一化失败路径诚实回退（`useUnified` 门控：% 轴与原始值永不同框）；App.vue 全局样式契约（.card/.banner/.up/.down/.flat）+ 错误态约定（view 内 catch → 主区 banner）。

## 9. 错误处理与数据质量

| 情形 | 行为 |
|------|------|
| 单源接口异常 | 重试 3 次后标记 failed；看板正常渲染其他源；坏源在状态条标红 + 显示最后成功日期 |
| 抓到 0 行 / 数据陈旧 | 标记 `empty`/`stale`，等同异常告警（防静默失败） |
| akshare 接口结构性失效 | fetcher fixture 测试在 CI/本地测试暴露；必要时为该源实现直连官方 API fallback（Fetcher 协议不变） |
| 非交易日/节假日 | 采集直接退出；前端时间轴只出现交易日 |
| T+1 数据 | 详情页与总览标注 `as_of_date`，异动榜对源更新滞后行标注实际日期（stale-tag） |
| Web 服务不可用 | compose `restart: unless-stopped` 自动拉起；数据无损 |
| 磁盘/库损坏 | 每日单文件备份保留 30 天（⚠️ 门控为"全部源成功"——存在顺延源期间不触发，可放宽门控或尽快落地顺延源）；全量历史可用 backfill 一条命令重建 |
| 调度器长跑 | 跨年日历耗尽防假死（上界降级 + 循环上限）；漏跑由启动补跑自愈 |

原则（借鉴 Resonance）：本地自洽、优雅降级（失败返回空/标记而不炸全局）、一条命令从零重建。

## 10. 测试策略

| 层 | 内容 | 工具 |
|----|------|------|
| Fetcher 单测 | 用真实接口录制的样本做 fixture，mock akshare/HTTP；断言输出 Observation 列表 | pytest |
| 幂等测试 | 同一交易日连续跑两遍，observations 行数与值不变 | pytest |
| 校验逻辑 | empty/stale/failed 判定与汇总报告 | pytest |
| 异动/归一化 | 环比计算、阈值过滤、基线归一化的数值正确性 | pytest |
| API 集成 | TestClient + 临时 SQLite，覆盖 6 个端点与边界（空源、缺日期） | pytest + httpx |
| 前端 | 归一化/chips 状态逻辑单测（Vitest）+ `npm run build` 冒烟 | Vitest |
| 真实接口冒烟 | `@pytest.mark.network` 手动运行，验证 akshare 未失效 | pytest |

## 11. 项目结构

```
data-tracking-platform/
├── backend/
│   ├── app/            # FastAPI：main.py / api/（sources/series/movements/jobs）/ db.py
│   ├── fetchers/       # base.py 注册表 + _util.py 公共工具 + 各源实现 + scrapers/
│   ├── scheduler.py    # 容器内置调度器（交易日历感知 + 启动补跑）
│   ├── collect.py      # 采集入口（含 --backfill、备份、webhook）
│   ├── seed.sql        # 数据源注册表种子（INSERT OR IGNORE）
│   └── tests/          # 75 项（含 fixtures/ 真实接口样本、gen_*_fixture.py 录制脚本）
├── frontend/           # Vue3 + Vite（src/{views,components,api.js,format.js}）
├── deploy/systemd/     # 备选：宿主机 systemd 单元
├── docs/               # PRD.md / superpowers/{specs,plans}/
├── Dockerfile          # 多阶段：node 构建前端 → python 运行时
├── docker-compose.yml  # web + scheduler（常驻）+ collect（--profile tools 按需）
└── pyproject.toml      # uv 管理（uv.lock 提交）
```

## 12. 扩展指南（SOP：新增一个数据源——四类 ETF 实战验证）

1. 在 `fetchers/` 实现 Fetcher：**复用 `_util.py`**（col/norm_code/clean_name/normalize_date + 共享列名候选常量，勿复制——复制已发生过漂移）；用真实接口录制 fixture 并写单测（含空 df/缺列 fail-loud/日期多形态）
2. `INSERT OR IGNORE INTO sources`（分组/单位/异动阈值/lag_days）+ `instruments`（成分）——重启任意服务时 init_db 自动补种
3. 若为新分组，侧边栏自动出现新组（group_name 驱动）；**前端与 API 零改动**
4. 回补历史：`docker compose run --rm collect uv run --no-sync python -m backend.collect --backfill --from ... --source <id>`（支持向前补历史/断点续跑；混合交易所源按代码前缀分流）
5. 验证：总览异动榜/详情页自动出现新数据源

实战耗时：四类 ETF（12 品种 + 混合 fetcher）约 0.5 人天；纯配置类源（已有 fetcher 复用）≈ 0.1 人天。多交易所源用一个按前缀分流的 fetcher 服务多 source（共享 targets 构建）。

## 13. 参考资料

- akshare 接口文档：https://akshare.akfamily.xyz/（fund_public / stock / futures / macro 各节，首批接口均实测）
- Resonance：https://github.com/OrangesHuang/Resonance（分层与降级原则）
- etf-national-tracker：https://github.com/kitaki-Ciallo/etf-national-tracker（看板结构、T+1 标注）
- ECharts 6：https://echarts.apache.org/
