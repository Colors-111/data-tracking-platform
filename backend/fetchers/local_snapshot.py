"""自选跟踪 fetcher（v1.3）：从 etf_snapshots 快照表读取注册品种的份额观测。
   注册（POST /api/watchlist）时已从快照表一次性回填全部历史 observations；
   本 fetcher 只负责未来日期——每日例行采集时 collect_market_snapshot 先把
   全市场快照写入 etf_snapshots（在逐源循环【之前】执行），再由本 fetcher
   把自选成分落成 observations，两条链路共用同一张快照表（零外部调用）。

   Fetcher 协议无 conn 参数：自开短连接（db_path() 尊重 DB_PATH 环境变量，
   与 backup_database 同口径）。快照表无 FK——覆盖全市场未注册代码。"""
import logging
import sqlite3

from ..app.db import db_path
from .base import Observation, register

log = logging.getLogger(__name__)


@register("local.snapshot")
class LocalSnapshotFetcher:
    source_id = "etf-watchlist"
    lag_days = 1

    def __init__(self, source, instruments):
        # code -> instrument_id（只产出已注册成分；id 即 6 位代码）
        self.targets = {i["code"]: i["id"] for i in instruments if i["code"]}

    def fetch(self, trade_date: str) -> list[Observation]:
        if not self.targets:
            return []
        p = db_path()
        if not p.exists():
            return []   # 测试/无库环境：无可读快照即无产出（不炸采集）
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        try:
            # 防御老库：表未建（init_db 前调用）时静默空产出
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table'"
                " AND name='etf_snapshots'").fetchone()
            if not has:
                return []
            rows = conn.execute(
                "SELECT code, shares FROM etf_snapshots WHERE trade_date=?",
                (trade_date,)).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            if r["code"] not in self.targets or r["shares"] is None:
                continue
            out.append(Observation(self.targets[r["code"]], "share",
                                   trade_date, float(r["shares"])))
        return out
