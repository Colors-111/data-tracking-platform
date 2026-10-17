"""采集入口：uv run python -m backend.collect [--backfill --from D --to D --source ID]
   目标日语义：run_date 之前第 lag_days 个【交易日】（日历日减法会在周一/节假日后
   指向非交易日，上交所按日快照接口只认交易日——周五快照将永久缺失）。"""
import argparse
import bisect
import datetime as dt
import functools
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential

from .app.db import connect, init_db
from .fetchers.base import build_fetcher
from .fetchers._util import (CODE_CANDIDATES, SHARE_CANDIDATES, clean_name,
                             col, norm_code)
from .notify import send_summary
from . import fetchers  # noqa: F401 触发注册

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

@functools.lru_cache(maxsize=1)
def _trade_days():
    """交易日历（升序 'YYYY-MM-DD' 元组，进程内缓存）；失败返回 None。"""
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        days = {str(d) for d in df["trade_date"].astype(str)}
        days = {d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days}
        return tuple(sorted(days))
    except Exception:
        return None

def previous_trade_date(run_date: str, n: int = 1) -> str:
    """run_date 之前第 n 个交易日（lag_days 语义）；日历不可用降级为第 n 个工作日。"""
    days = _trade_days()
    if not days:
        log.warning("交易日历获取失败，按工作日推算目标日")
        d = dt.date.fromisoformat(run_date)
        for _ in range(n):
            d -= dt.timedelta(days=1)
            while d.weekday() >= 5:
                d -= dt.timedelta(days=1)
        return d.isoformat()
    i = max(bisect.bisect_left(days, run_date) - n, 0)
    return days[i]

def trade_dates_from_to(d_from: str, d_to: str) -> list[str]:
    """[d_from, d_to] 内的交易日（升序）；日历失败降级为工作日。"""
    days = _trade_days()
    if days is None:
        log.warning("交易日历获取失败，降级为工作日")
        out, cur = [], dt.date.fromisoformat(d_from)
        while cur <= dt.date.fromisoformat(d_to):
            if cur.weekday() < 5:
                out.append(cur.isoformat())
            cur += dt.timedelta(days=1)
        return out
    return list(days[bisect.bisect_left(days, d_from):bisect.bisect_right(days, d_to)])

def run_source(conn, source, instruments, run_date: str) -> dict:
    started = dt.datetime.now().isoformat(timespec="seconds")
    result = {"source_id": source["id"], "rows": 0, "status": "ok", "error": None}
    try:
        fetcher = build_fetcher(source["fetcher"], source, instruments)
        target = previous_trade_date(run_date, fetcher.lag_days)
        obs = fetch_with_retry(fetcher, target)
        upsert_observations(conn, obs, started)
        result["rows"] = len(obs)
        result["target_date"] = target
        # fetch 是否真的覆盖了目标日：精确日期源成功时恒 True；
        # 窗口型源（如库存，fetch 返回近期整段）取历史目标时 False——backfill 据此提前止损
        result["covered"] = any(o.trade_date == target for o in obs)
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

def backup_database(conn, keep: int = 30) -> str:
    """每日采集成功后单文件备份（SQLite backup API，WAL 安全），滚动保留 keep 份。"""
    db = Path(os.environ.get("DB_PATH", "data/dtp.db"))
    bdir = db.parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"dtp-{dt.date.today().isoformat()}.db"
    if not dest.exists():
        dst = sqlite3.connect(dest)
        with dst:
            conn.backup(dst)
        dst.close()
    for old in sorted(bdir.glob("dtp-*.db"))[:-keep]:
        old.unlink()
    return str(dest)

# ---------- v1.3 全市场 ETF 份额快照（设计 §5.3） ----------

_snapshot_retry = retry(stop=stop_after_attempt(3),
                        wait=wait_exponential(min=2, max=30), reraise=True)

@_snapshot_retry
def _fetch_sse_snapshot(d8: str):
    """上交所全市场 ETF 份额单日快照（与 etf_scale fetcher 同接口）。"""
    import akshare as ak
    return ak.fund_etf_scale_sse(d8)

@_snapshot_retry
def _fetch_szse_snapshot(d8: str):
    """深交所全市场 ETF 份额单日区间（start_date/end_date 为实测签名；
    近期间歇性 connection refused——由 collect_market_snapshot 逐所隔离）。"""
    import akshare as ak
    return ak.fund_scale_daily_szse(start_date=d8, end_date=d8, symbol="ETF")

def _snapshot_branch(df, trade_date: str, exchange: str, now_iso: str) -> list[tuple]:
    """两所快照 df → etf_snapshots 行元组（code/份额列按共享候选常量
       fail-loud 定位；份额 份 → 亿份，两所接口同口径）。空 df 返回 []。"""
    if df is None or df.empty:
        return []
    c_code = col(df, CODE_CANDIDATES, "代码")
    c_share = col(df, SHARE_CANDIDATES, "份额")
    return [(trade_date, norm_code(r[c_code]), clean_name(r.get("基金简称")),
             exchange, float(r[c_share]) / 1e8, now_iso)
            for _, r in df.iterrows()]

def collect_market_snapshot(conn, target_date: str, now_iso: str) -> int:
    """全市场 ETF 份额快照落 etf_snapshots（PK(trade_date, code) 幂等）。
       逐所 try/except：任一所失败（含重试耗尽）只 log.warning 跳过——
       快照采集独立于逐源 run_source，失败不写 job_runs、不影响退出码。
       单所失败留下的部分日不算已覆盖，由下次回补预跑的逐所覆盖门控
       自动补齐（INSERT OR REPLACE 幂等，见 main() 快照预跑）。
       返回本次写入行数（两所全失败/全空 → 0，绝不抛出）。"""
    d8 = target_date.replace("-", "")
    written = 0
    for exchange, fetch in (("SSE", _fetch_sse_snapshot),
                            ("SZSE", _fetch_szse_snapshot)):
        try:
            rows = _snapshot_branch(fetch(d8), target_date, exchange, now_iso)
        except Exception as e:
            log.warning("全市场快照 %s 采集失败，跳过（其余所照常，该日由下次"
                        "回补预跑的逐所门控补齐）: %s", exchange, e)
            continue
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO etf_snapshots
                     (trade_date, code, name, exchange, shares, fetched_at)
                   VALUES (?,?,?,?,?,?)""", rows)
            written += len(rows)
    conn.commit()
    log.info("全市场快照 %s 写入 %d 行", target_date, written)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--from", dest="d_from")
    ap.add_argument("--to", dest="d_to", default=dt.date.today().isoformat())
    ap.add_argument("--source", help="只跑指定 source id（回补/调试用）")
    ap.add_argument("--force", action="store_true",
                    help="backfill 忽略 covered 跳过：给已有源扩成分后强制重抓历史"
                         "（幂等 upsert，不重不漏）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    # 不显式 close：CLI 进程退出即释放（各路径均已显式 commit）；
    # 显式关闭会切断测试经 monkeypatch 注入的共享连接
    conn = connect()
    init_db(conn)
    where, params = ("WHERE s.enabled=1", ()) if not args.source else \
                    ("WHERE s.id=?", (args.source,))
    sources = conn.execute(f"SELECT * FROM sources s {where} ORDER BY sort_order",
                           params).fetchall()

    if args.backfill:
        if not args.d_from:
            ap.error("--backfill 需要 --from 起始日期")
        dates = trade_dates_from_to(args.d_from, args.d_to)
        if not dates:
            log.error("区间内无交易日：%s ~ %s", args.d_from, args.d_to)
            return 1
        log.info("回补 %s ~ %s，共 %d 个交易日", args.d_from, args.d_to, len(dates))
        # v1.3 快照历史预回补：全市场资金流与自选跟踪的历史都在 etf_snapshots，
        # 逐源循环前按同 target 语义（前一交易日）补齐快照（一次回补即得全史）。
        # --source 指定其他源时跳过（不触网）；--source etf-watchlist 时也在
        # 门控内——该源补跑必须先有快照才有观测可产出（调度器逐源补跑自愈依赖）。
        if not args.source or args.source == "etf-watchlist":
            now_iso = dt.datetime.now().isoformat(timespec="seconds")
            for d in dates:
                target = previous_trade_date(d, 1)
                # 逐所覆盖门控：该日 SSE+SZSE 两所行齐全才算覆盖（重跑零开销）；
                # --force 不覆盖此门控（INSERT OR REPLACE 本就幂等，重抓全史纯属
                # 浪费）；单所失败留下的部分日不算已覆盖——下次回补预跑自动补齐
                if conn.execute(
                        "SELECT COUNT(DISTINCT exchange) c FROM etf_snapshots"
                        " WHERE trade_date=?", (target,)).fetchone()["c"] == 2:
                    continue
                collect_market_snapshot(conn, target, now_iso)
        failed = []
        for s in sources:
            insts = conn.execute(
                "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
                (s["id"],)).fetchall()
            # lag 从 fetcher 取（未注册 fetcher 的源 run_source 会正常标 failed，lag 用配置值兜底）
            try:
                lag = build_fetcher(s["fetcher"], s, insts).lag_days
            except KeyError:
                lag = s["lag_days"]
            for d in dates:
                # 按【目标日】判覆盖：库里已有该源目标日的数据才跳过——
                # 支持在已有较新日常数据时向前补历史（旧规则 d<=max 会把历史全部误跳）。
                # --force：给已有源扩成分后新成分无历史，忽略 covered 强制重抓
                target = previous_trade_date(d, lag)
                covered = conn.execute(
                    """SELECT 1 FROM observations o
                         JOIN instruments i ON i.id=o.instrument_id
                        WHERE i.source_id=? AND o.trade_date=? LIMIT 1""",
                    (s["id"], target)).fetchone()
                if covered and not args.force:
                    continue
                r = run_source(conn, s, insts, d)
                if r["status"] == "failed":
                    failed.append((s["id"], d))
                    break   # 断点续跑：保留现场，下次回补从该日重试
                if (r["rows"] > 0
                        and not r.get("covered")
                        and (h := source_max_date(conn, s["id"]))
                        and h > r["target_date"]):
                    # 窗口型源：fetch 返回非空行但不含目标日（只覆盖近期），向更早回补不会再有产出。
                    # 0 行（上市前空日）不算——否则并发日更跳高 source_max_date 会把空日误判成窗口型
                    log.info("源 %s 为窗口型（fetch 不含目标日，仅覆盖近期），停止向更早回补",
                             s["id"])
                    break
        if failed:
            log.error("回补存在失败源/日期：%s（修复后重跑 --backfill 即续传）", failed)
            return 1
        return 0

    results = []
    if not args.source:
        # v1.3 全市场快照（设计 §5.3）：必须在逐源循环【前】写入——自选源
        # local.snapshot 从快照表读本次目标日（前一交易日）。若放循环后，
        # 循环读到的永远是"上一次运行循环后写入的 T-2 快照"，目标日恒差
        # 一天，etf-watchlist 将 perpetually empty（时间线推演见设计 §5.3）
        collect_market_snapshot(conn, previous_trade_date(args.date, 1),
                                dt.datetime.now().isoformat(timespec="seconds"))
    for s in sources:
        insts = conn.execute(
            "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
            (s["id"],)).fetchall()
        results.append(run_source(conn, s, insts, args.date))
    send_summary(results)
    bad = [r for r in results if r["status"] == "failed"]
    # 仅 failed 非零退出（触发 systemd OnFailure 崩溃告警）；
    # empty/stale 属数据质量事件，经 job_runs 与 Task 8 的汇总 webhook 报告，不算崩溃
    if not bad:
        try:
            backup_database(conn)   # 设计 §6：全部成功后备份，保留 30 天
        except Exception:
            # 备份失败只告警不改 rc——否则会被当成"采集崩溃"触发 OnFailure 误告警
            log.warning("数据库备份失败", exc_info=True)
    log.info("完成：%d 源，failed=%d empty/stale=%d", len(results), len(bad),
             sum(1 for r in results if r["status"] in ("empty", "stale")))
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
