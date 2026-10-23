"""v1.3 全市场 ETF 资金流 API（设计 §5.3）。
   GET  /api/flows      资金异动榜——全市场快照环比（LAG 窗口）
   POST /api/watchlist  一键注册自选跟踪：写 instruments + 从 etf_snapshots
                        回填该 code 全部历史 observations（零外部调用）
   本项目首个写端点：内网无鉴权原则不变，唯一写路径为自选注册（幂等）。"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import get_conn

router = APIRouter()


@router.get("/api/flows")
def flows(date: str = Query(None), limit: int = Query(10, ge=1, le=100),
          conn=Depends(get_conn)):
    as_of = date or conn.execute(
        "SELECT MAX(trade_date) m FROM etf_snapshots").fetchone()["m"]
    if not as_of:
        return {"as_of_date": None, "total_change": None, "count": 0,
                "top_in": [], "top_out": []}
    rows = conn.execute(
        """WITH w AS (
             SELECT code, name, exchange, trade_date, shares,
                    LAG(shares) OVER (PARTITION BY code ORDER BY trade_date)
                      AS prev_shares
               FROM etf_snapshots WHERE trade_date <= :as_of)
           SELECT code, name, exchange, shares, prev_shares FROM w
            WHERE trade_date = :as_of""",
        {"as_of": as_of}).fetchall()
    changed, total = [], 0.0
    for r in rows:
        # 无前值/前值为 0/份额缺失不计入变动榜（与 movements 的 prev!=0 同规则）
        if (r["shares"] is None or r["prev_shares"] is None
                or r["prev_shares"] == 0):
            continue
        change = round(r["shares"] - r["prev_shares"], 4)
        total += change
        changed.append({
            "code": r["code"], "name": r["name"], "exchange": r["exchange"],
            "shares": r["shares"], "change": change,
            "pct": round((r["shares"] / r["prev_shares"] - 1) * 100, 2),
        })
    changed.sort(key=lambda x: (-x["change"], x["code"]))   # 平分按代码稳定排序
    # 榜按符号过滤：净申购榜只收 change>0、净赎回榜只收 change<0——
    # 持平代码不上任何榜，单边净申购（赎回）日反向榜为空而非被填满
    return {"as_of_date": as_of,
            "total_change": round(total, 4) if changed else None,
            "count": len(rows),           # 全市场快照当日全部代码数
            "top_in": [x for x in changed if x["change"] > 0][:limit],
            "top_out": [x for x in changed if x["change"] < 0][::-1][:limit]}


class WatchlistIn(BaseModel):
    code: str


@router.post("/api/watchlist")
def add_watchlist(body: WatchlistIn, conn=Depends(get_conn)):
    code = body.code.strip().zfill(6)   # 6 位代码：instruments.id 与 code 一致
    snap = conn.execute(
        """SELECT name FROM etf_snapshots WHERE code=?
           ORDER BY trade_date DESC LIMIT 1""", (code,)).fetchone()
    if not snap:
        raise HTTPException(404, f"快照表中无该代码：{code}")
    name = snap["name"] or code
    # 已注册于其他源的同 id instrument（如 510300 ∈ hs300-etf）不迁移不覆盖：
    # 冲突忽略，历史观测仍按 instrument_id 回填并归属原源（保持注册语义简单）
    conn.execute(
        """INSERT INTO instruments(id, source_id, code, metric, unit, name, sort_order)
           VALUES (?, 'etf-watchlist', ?, 'share', '亿份', ?,
                   (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM instruments
                     WHERE source_id='etf-watchlist'))
           ON CONFLICT(id) DO NOTHING""", (code, code, name))
    backfill = conn.execute(
        """INSERT INTO observations
             (instrument_id, metric, trade_date, value, raw_json, fetched_at)
           SELECT code, 'share', trade_date, shares, NULL, ?
             FROM etf_snapshots WHERE code=? AND shares IS NOT NULL
           ON CONFLICT DO NOTHING""",
        (dt.datetime.now().isoformat(timespec="seconds"), code))
    conn.commit()
    return {"registered": True, "name": name,
            "history_rows": max(backfill.rowcount, 0)}
