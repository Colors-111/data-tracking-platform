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
        failed = []
        for s in sources:
            for d in dates:
                # 已有数据则跳过：精确日期源逐日推进；窗口型源（如库存，fetch
                # 忽略日期返回整段近60日）首日抓完即整段跳过，避免数千次重复抓取
                if (h := source_max_date(conn, s["id"])) and d <= h:
                    continue
                insts = conn.execute(
                    "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
                    (s["id"],)).fetchall()
                r = run_source(conn, s, insts, d)
                if r["status"] == "failed":
                    failed.append((s["id"], d))
                    break   # 断点续跑：保留现场，下次回补从该日重试
        if failed:
            log.error("回补存在失败源/日期：%s（修复后重跑 --backfill 即续传）", failed)
            return 1
        return 0

    results = []
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
