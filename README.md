# data-tracking-platform
追踪etf、期货等数据

## 快速开始（内网部署）

```bash
# 1) 前置：要求部署用户 uid=1000（compose 固定 user: "1000:1000"）。
#    全新机器先 `mkdir -p data`（避免 Docker daemon 以 root 创建挂载源目录）；
#    若 data/ 已被 root 污染：`sudo chown -R 1000:1000 data/`
# （可选）配置告警 webhook：项目根创建 .env，写一行 WEBHOOK_URL=https://...

# 2) 构建镜像（API 服务随 Task 11 上线后 docker compose up -d web 即可用；
#    在此之前 up web 会因 main.py 未实现而重启循环，属预期）
docker compose build

# 3) 首次回补历史（默认全量：上交所份额自 2015-01-01，约 2500 个交易日，需 40 分钟量级；
#    如需快速验证可先 --from 2026-01-01，之后随时用 --backfill 断点续跑补齐）
docker compose run --rm collect uv run --no-sync python -m backend.collect \
  --backfill --from 2015-01-01

# 4) 定时采集（宿主机 systemd；要求宿主机时区为 Asia/Shanghai）
sudo cp deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now data-collect.timer
```
