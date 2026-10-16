# 数据追踪平台 · 设计文档

版本：v1.0 ｜ 日期：2026-10-16 ｜ 状态：待评审
产品需求见 [docs/PRD.md](../../PRD.md)

---

## 1. 概述

内网单机部署的数据追踪平台：每日收盘后从公开数据源自动采集（Python/akshare），存入 SQLite(WAL)，FastAPI 提供只读 API 并托管 Vue3 + ECharts 前端。核心设计约束（来自用户）：**第一版界面按参考图实现，架构必须为后续新数据源、新功能预留适配性**——落地方式为"数据源注册表驱动一切"。

## 2. 总体架构

```
┌────────────────────────────────────────────────────────┐
│ 内网 Linux VM                                          │
│                                                        │
│  systemd timer (data-collect.timer)                    │
│  OnCalendar=Mon..Fri 16:40  Persistent=true            │
│  OnFailure=data-notify@%n                              │
│        │                                               │
│        ▼                                               │
│  collect.py（跑完即退，非交易日直接退出）                  │
│  遍历 Fetcher 注册表 → 逐源独立执行                        │
│  tenacity 重试 → 幂等 upsert → 校验 → job_runs → webhook │
│        │                                               │
│        ▼                                               │
│  SQLite 单文件（WAL 模式）                               │
│        ▲ 只读连接池                                     │
│  uvicorn: FastAPI（唯一常驻服务，端口 8000）               │
│  ├─ /api/* 只读 JSON API                                │
│  └─ StaticFiles 托管 frontend/dist（SPA fallback）      │
│        ▲                                               │
│  团队浏览器 http://<内网IP>:8000                         │
│  Vue3 + Vite + vue-echarts（ECharts 6）                 │
└────────────────────────────────────────────────────────┘
```

要点：

- **运行环境整体容器化（用户约束）**：前后端构建产物打进同一 Docker 镜像，`docker compose` 编排 `web`（常驻）与 `collect`（按需运行）；宿主机只需安装 Docker，Python/Node 环境完全隔离
- 除 FastAPI 外**零常驻进程**；采集器与 Web 服务通过 SQLite 解耦（WAL 下读不阻塞写）
- 单源失败不阻断：逐源 try/except，失败的源标记 `failed`，看板其他部分照常
- 告警双通道：脚本收尾统一推送汇总（成功行数/失败原因/新鲜度）到 webhook；进程级崩溃由 systemd `OnFailure=` 模板单元兜底（curl webhook，无需 MTA）

## 3. 技术栈

| 层 | 选型 | 版本/说明 |
|----|------|-----------|
| 语言/包管理 | Python + uv | Python 3.12，`uv sync` 一键装依赖 |
| 数据接口 | akshare | `>=1.19.1`（修复广期所仓单接口）+ tenacity 重试 |
| 存储 | SQLite (WAL) | `journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000` |
| Web | FastAPI + uvicorn | 只读 API，StaticFiles 托管前端构建产物 |
| 前端 | Vue3 + Vite + vue-echarts | ECharts 6（dataZoom/tooltip/图例开关/双Y轴原生支持） |
| 样式 | 纯 CSS | 深色侧边栏贴参考图，不引 UI 框架 |
| 环境隔离 | Docker + docker compose | 多阶段构建：`node:20-slim` 编译前端 → `python:3.12-slim` + uv 运行时；宿主机仅装 Docker |
| 部署 | docker compose + 宿主机 systemd timer | timer 触发 `docker compose run --rm collect`，保留补跑与 OnFailure 告警 |

明确排除项及理由（调研结论）：Grafana（无法实现参考图的定制布局/导航树，SQLite 插件限制多）、Streamlit（样式受组件库约束、多页交互弱）、Evidence.dev（动态对比交互弱）、DuckDB 主存储（读写进程互斥，与常驻 Web 连接冲突）、APScheduler/Prefect/Airflow（systemd timer 已覆盖补跑/告警/日志，编排平台属过重）。

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
  lag_days      INTEGER DEFAULT 0,       -- 数据发布滞后（T+1 源=1）；采集目标日=交易日-lag_days
  enabled       INTEGER DEFAULT 1,
  sort_order    INTEGER
);

-- 成分品种：属于某数据源的可观测个体
CREATE TABLE instruments (
  id          TEXT PRIMARY KEY,          -- '510300'
  source_id   TEXT REFERENCES sources(id),
  code        TEXT,                      -- '510300' / NULL（单值源如煤炭价格）
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
├── base.py        # Fetcher 协议 + 注册表（@register 装饰器）
├── akshare_*.py   # akshare 适配器
└── scrapers/      # 自研轻爬虫（SCFI、动力煤，第三批）
```

Fetcher 协议（每个数据源实现一个）：

```python
class Fetcher(Protocol):
    source_id: str
    lag_days: int            # 数据发布滞后天数（如上交所份额 T+1 → 1）
    def fetch(self, trade_date: date) -> list[Observation]:
        """返回该源当日全部观测；失败抛异常，由执行器捕获"""
```

注册表由 `sources.fetcher` 字符串解析加载 → **新增数据源不改执行器**。

### 5.1 首批三源的接口映射（已实测验证）

| source | fetcher | akshare 接口 | 备注 |
|--------|---------|--------------|------|
| 沪深300 ETF 份额 | `akshare.sse_etf_scale` | `fund_etf_scale_sse(date)` | 当日全上交所 ETF 快照，历史回溯到 2015；T+1，看板须标注实际日期 |
| 电解铜库存 | `akshare.cu_inventory` | `futures_inventory_em('cu')` | **仅保留近 60 交易日，每次抓取把返回的完整区间全部 upsert**，尽早开始累积 |
| 煤炭价格 | `scrapers.coal_price` | 无可用接口 | 动力煤现货价轻爬虫（CCTD/生意社页面）。实现排在 M1 后半程至 M2；若爬虫受阻（登录墙/反爬），M1 先以沪深300+电解铜库存验证全链路，煤炭价格顺延，**不用焦煤 JM 等近似品种替代**（数据口径会误导投研），v1 范围不变 |

批次二接口映射（设计已预留，实现时按 PRD 5.2 逐个加入）：两融 `macro_china_market_margin_sh/sz` + `stock_margin_sse/szse` 增量；商品价格 `futures_spot_price(vars_list=['CU','AL','AO','LC'])`；上期所仓单 `futures_shfe_warehouse_receipt`；碳酸锂仓单 `futures_gfex_warehouse_receipt`（≥1.19.1，仍坏则直连 gfex POST，字段 `regWbillQty→diff`）；黄金储备 `macro_china_foreign_exchange_gold`。

## 6. 采集管道

`backend/collect.py` 执行流程：

1. **交易日判断**：`tool_trade_date_hist_sina` 取交易日历，非交易日 exit 0
2. **逐源执行**：遍历 `sources WHERE enabled=1`；每源独立 try/except + tenacity 重试 3 次（指数退避）；单源异常记录 `job_runs.status='failed'` 继续
3. **写入**：`INSERT OR REPLACE INTO observations`（唯一键幂等，重跑/补跑安全）；库存类 fetcher 每次返回完整近 60 日区间全部 upsert
4. **校验**（防静默失败）：今日新增 0 行 → `empty`；最新观测日期 < 最近交易日 → `stale`；均视为异常
5. **汇总**：写 job_runs；有异常或全部成功均推 webhook 汇总（源、行数、状态、错误摘要）；无 webhook 配置时仅落库与日志
6. **备份**：全部成功后 `sqlite3 db ".backup backups/$(date).db"`，保留 30 天

**历史回补**：`collect.py --backfill --from 2015-01-01 [--source hs300-etf]`——逐交易日循环调用（上交所接口按日快照，天然支持回补）；支持断点续跑（跳过已有日期）。**历史深度原则**：数据源能提供多深就回补多深（上交所份额 2015 年起 ≈11 年）；只提供短窗口的源（东财库存 60 交易日）无法回补，靠每日采集持续累积，以 5-10 年为目标——上线即可用，深度随时间自然增长。

**systemd 部署**（宿主机，`deploy/systemd/`）：

```ini
# data-collect.timer
[Timer]
OnCalendar=Mon..Fri *-*-* 16:40:00
Persistent=true

# data-collect.service
[Service]
ExecStart=docker compose -f /opt/dtp/docker-compose.yml run --rm collect
# 失败兜底：OnFailure=data-notify@%n → curl $WEBHOOK_URL "采集进程崩溃: %i"
```

**容器化与部署（docker compose）**：

- **单一镜像、多阶段构建**：阶段 1 `node:20-slim` 执行 `npm ci && npm run build` 产出 `dist/`；阶段 2 `python:3.12-slim` + uv 安装后端依赖并拷入 `dist/`。`web` 与 `collect` 共用该镜像，杜绝"构建环境/运行环境"污染宿主机
- **compose 服务**：
  - `web`：`8000:8000`，`restart: unless-stopped`，挂载 `./data:/app/data`
  - `collect`：同镜像，由宿主机 systemd timer `docker compose run --rm collect` 按需执行（保留 Persistent 补跑与 OnFailure 告警能力）
- **环境变量**：`TZ=Asia/Shanghai`（容器内交易日判断与时间戳必须用北京时间）、`WEBHOOK_URL`、`DB_PATH=/app/data/dtp.db`
- SQLite WAL 运行于本地 bind mount（满足"非 NFS"约束）；数据库与备份（`./data/backups`）均在宿主机卷上，容器可随时重建
- 开发/测试同样走容器（`docker compose run --rm collect pytest`、`npm run build` 在镜像构建中完成），保证编译与运行环境隔离；本机 uv venv 仅作为可选快速迭代路径

## 7. API 设计

| Method | Path | 用途 |
|--------|------|------|
| GET | `/api/sources` | 侧边栏：分组数据源列表 + 每源最新状态/日期 |
| GET | `/api/sources/{id}?range=3m` | 详情页数据：成分卡片（最新值/日变化/区间变化）+ 各成分序列 |
| GET | `/api/series?instruments=a,b&metric=share&range=6m&normalize=1` | 跨源取序列，供对比 chips；normalize=1 时返回基线 100 归一化 |
| GET | `/api/movements?date=today` | 今日异动榜：跨源环比超过 `movement_th` 按 \|pct\| 降序 |
| GET | `/api/jobs/latest` | 抓取状态条：各源最近一次 job_runs |
| GET | `/api/meta/trading-dates` | 可选时间范围（v1 前端用固定档位切换，此端点延后实现） |

约定：所有序列响应携带 `as_of_date`（数据实际日期）；Pydantic schema 定义于 `backend/app/schemas.py`；API 只读，无鉴权（内网）。

## 8. 前端设计

```
frontend/src/
├── views/
│   ├── OverviewView.vue      # 总览：MovementsTable + SourceTiles
│   └── SourceDetailView.vue  # 详情：SourceHeader + CardRow + CompareChips + TrendChart
├── components/
│   ├── SideNav.vue           # 分组导航（/api/sources 驱动）
│   ├── MovementsTable.vue    # 异动榜（红涨绿跌、5日sparkline、行下钻）
│   ├── CardRow.vue / MetricCard.vue
│   ├── TrendChart.vue        # vue-echarts 封装：多线/图例开关/dataZoom/tooltip
│   ├── CompareChips.vue      # 已加序列 chips + 「+添加对比」选择器 + 归一化开关
│   └── JobStatus.vue         # 数据截至日期 + 抓取状态
└── api/                      # fetch 封装与类型
```

路由：`/`（总览）、`/source/:id`（详情）。渲染完全由 API 数据驱动：侧边栏分组、卡片字段（名称/值/单位）、曲线数量均来自 `sources`/`instruments` 配置——**新增数据源前端零改动**。深色侧边栏 + 浅色主区 + 红涨绿跌贴参考图；图表配色采用固定分类色板保证跨页一致。

## 9. 错误处理与数据质量

| 情形 | 行为 |
|------|------|
| 单源接口异常 | 重试 3 次后标记 failed；看板正常渲染其他源；坏源在状态条标红 + 显示最后成功日期 |
| 抓到 0 行 / 数据陈旧 | 标记 `empty`/`stale`，等同异常告警（防静默失败） |
| akshare 接口结构性失效 | fetcher fixture 测试在 CI/本地测试暴露；必要时为该源实现直连官方 API fallback（Fetcher 协议不变） |
| 非交易日/节假日 | 采集直接退出；前端时间轴只出现交易日 |
| T+1 数据 | 详情页与总览在图表角落/徽章标注 `as_of_date`，不与自然日混淆 |
| Web 服务不可用 | systemd `Restart=on-failure` 自动拉起；数据无损 |
| 磁盘/库损坏 | 每日单文件备份保留 30 天；全量历史可用 backfill 一条命令重建 |

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
│   ├── app/            # FastAPI：main.py / api/ / db.py / schemas.py
│   ├── fetchers/       # base.py 注册表 + 各源实现
│   ├── collect.py      # 采集入口（含 --backfill）
│   └── tests/
├── frontend/           # Vue3 + Vite
├── deploy/systemd/     # 宿主机 *.service *.timer（触发 docker compose run）
├── docs/               # PRD.md / superpowers/specs/
├── Dockerfile          # 多阶段：node 构建前端 → python 运行时
├── docker-compose.yml  # web（常驻）+ collect（按需）
└── pyproject.toml      # uv 管理
```

## 12. 扩展指南（SOP：新增一个数据源）

1. 在 `fetchers/` 实现 Fetcher（协议见 §5），用真实样本录制 fixture 并写单测
2. `INSERT INTO sources`（分组/单位/异动阈值）+ `INSERT INTO instruments`（成分）
3. 若为新分组，侧边栏自动出现新组（group_name 驱动）
4. 回补历史：`collect.py --backfill --from ... --source <id>`
5. 验证：总览异动榜/详情页自动出现新数据源，前端零改动

预计耗时 ≤ 0.5 人天/源（爬虫类视难度）。

## 13. 参考资料

- akshare 接口文档：https://akshare.akfamily.xyz/（fund_public / stock / futures / macro 各节，首批接口均实测）
- Resonance：https://github.com/OrangesHuang/Resonance（分层与降级原则）
- etf-national-tracker：https://github.com/kitaki-Ciallo/etf-national-tracker（看板结构、T+1 标注）
- ECharts 6：https://echarts.apache.org/
