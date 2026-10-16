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
        from .notify import send_summary  # 延迟导入（notify 随 Task 8 落地后可上移到模块顶部）
        send_summary(results)
        bad = [r for r in results if r["status"] != "ok"]
        log.info("完成：%d 源，%d 异常", len(results), len(bad))
        conn.close()
        return 1 if bad else 0

    conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
