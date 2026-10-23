# data-tracking-platform
追踪etf、期货等数据

内网看板：ETF 份额与资金流、大宗商品（铜/铝/氧化铝/黄金）、融资融券（市场杠杆资金）。当前 15 个数据源、四个侧边栏分组（指数 ETF / 大宗商品 / 自选跟踪 / 市场资金），历史按源可达上限回补（ETF 11 年、SHFE 21 年、CFTC 40 年、央行储备 48 年）。产品与设计文档见 [docs/PRD.md](docs/PRD.md) 与 [docs/superpowers/specs/](docs/superpowers/specs/)。

## 快速开始（内网部署）

```bash
# 1) 前置：要求部署用户 uid=1000（compose 固定 user: "1000:1000"）。
#    全新机器先 `mkdir -p data`（避免 Docker daemon 以 root 创建挂载源目录）；
#    若 data/ 已被 root 污染：`sudo chown -R 1000:1000 data/`
# （可选）配置告警 webhook：项目根创建 .env，写一行 WEBHOOK_URL=https://...

# 2) 构建镜像（--profile tools 必须：collect 服务带 profiles 标签，
#    裸 build 只构建 web、collect 镜像会停留在旧版）
docker compose --profile tools build

# 3) 一条命令全起：web（API，http://<内网IP>:8000）+ scheduler（容器内置调度器：
#    交易日 09:00/16:40 双档自动采集——09:00 档抓前一交易日（T+1 源次日早上可见）、
#    16:40 档抓当日（含完整性保险，当日数据当日可见）；启动/例行后自动补跑漏掉的
#    交易日，无需宿主机 systemd/cron）
docker compose up -d

# 4) 首次回补历史（默认全量：上交所份额自 2015-01-01，约 2500 个交易日，需 40 分钟量级；
#    如需快速验证可先 --from 2026-01-01，之后随时用 --backfill 断点续跑补齐）
docker compose run --rm collect uv run --no-sync python -m backend.collect \
  --backfill --from 2015-01-01

# 5) 运维速查
docker compose logs -f scheduler           # 调度器日志：下次触发时间/补跑/采集结果
docker compose logs -f web                 # API 日志
# 如需宿主机 systemd 方式（备选，要求宿主机时区 Asia/Shanghai）：deploy/systemd/ 内单元可用
# 备份在 data/backups/，恢复 = 停服务后用备份文件覆盖 data/dtp.db
```
