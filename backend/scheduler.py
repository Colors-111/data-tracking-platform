"""常驻调度器：交易日 16:40（Asia/Shanghai）触发采集；启动时补跑漏掉的交易日。
   替代宿主机 systemd timer（docker 原生、日历感知、自带补跑）。

   用法：uv run python -m backend.scheduler

   语义要点：
   - 例行跑（当日 16:40）目标日 = 前一交易日（lag=1），故"昨日交易日"是源是否
     落后的判定基准线；今天的数据由今日 16:40 例行跑覆盖，不算错过；
   - missed_dates/catchup 只看已有数据的源：max 为 NULL 视为未初始化，
     归首次手动回补管（README 快速开始的 --backfill 步骤），否则新库会被判
     "缺失全部历史"；
   - 回补区间 d_to=昨日：今日例行跑会覆盖最新目标日，避免与之重复 fetch；
     若某日例行失败，次日启动补跑会把缺口自动追上（backfill 断点续跑语义）。"""
import datetime as dt
import logging
import time

from .app.db import connect, init_db
from . import collect as C

log = logging.getLogger("scheduler")
RUN_TIME = dt.time(16, 40)


def _trade_days():
    """交易日历（复用 collect 的缓存实现）；失败返回 None（降级工作日）。"""
    return C._trade_days()


def _is_trade_day(d: dt.date) -> bool:
    days = _trade_days()
    if not days or d.isoformat() > days[-1]:
        # 无日历 / 空日历 / 日期超出日历上界（新浪日历只覆盖到当年年末，常驻进程
        # 不停机跨年后 d 恒不在 days，会让 next_run 死循环假死）→ 按工作日降级判定
        return d.weekday() < 5
    return d.isoformat() in days


def next_run(now: dt.datetime) -> dt.datetime:
    """下一个交易日 16:40（本地时间；容器 TZ=Asia/Shanghai）。

    日历耗尽双保险：_is_trade_day 对超出日历上界的日期按工作日降级；此处再设
    400 次迭代上限，超限告警并按工作日推算返回——常驻进程跨年绝不假死。
    """
    candidate = now.replace(hour=RUN_TIME.hour, minute=RUN_TIME.minute,
                            second=0, microsecond=0)
    # 恰为 16:40:00.000000（candidate == now）也顺延：主循环 sleep 醒来于
    # 16:40:00.00x 后才重算此分支，恒等情形实际不可达，纯防御
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    for _ in range(400):
        if _is_trade_day(candidate.date()):
            return candidate
        candidate += dt.timedelta(days=1)
    log.error("连续 400 日未找到交易日（交易日历异常？），按工作日降级推算下次采集")
    while candidate.weekday() >= 5:
        candidate += dt.timedelta(days=1)
    return candidate


def _stale_sources(conn, today: dt.date):
    """落后的启用源 → [(source, max_d, d_from, horizon)]。

    单源基准线 horizon = previous_trade_date(today, lag)：这是今日（或下一个）
    例行跑会覆盖到的目标日；source_max_date < horizon 即尾部欠账。
    d_from = max+1（日历日；backfill 内部按交易日迭代，非交易日自动跳过）。
    """
    out = []
    sources = conn.execute(
        "SELECT * FROM sources WHERE enabled=1 ORDER BY sort_order").fetchall()
    for s in sources:
        max_d = C.source_max_date(conn, s["id"])
        if not max_d:
            continue   # 无数据 = 未初始化（首次回补手动做），不算调度欠账
        horizon = C.previous_trade_date(today.isoformat(), s["lag_days"])
        if max_d >= horizon:
            continue
        d_from = (dt.date.fromisoformat(max_d) + dt.timedelta(days=1)).isoformat()
        out.append((s, max_d, d_from, horizon))
    return out


def missed_dates(conn, today: dt.date) -> list[str]:
    """跨源诊断：各启用源当前缺失的交易日（并集，升序）。

    单源缺失 = (source_max_date, 昨日交易日] 内的交易日——max 是该源已覆盖的
    最高目标日，其上的目标日必然未写，区间即欠账（对齐测试断言：max=10-13、
    今日 10-16 → 返回 10-14、10-15，不含 10-16）。
    """
    out: set[str] = set()
    for _s, _max_d, d_from, horizon in _stale_sources(conn, today):
        out.update(C.trade_dates_from_to(d_from, horizon))
    return sorted(out)


def _run_catchup_backfill(conn, source, d_from: str) -> int:
    """单源回补 d_from ~ 昨日，经 C.main 的 backfill 模式执行，返回其退出码。

    连接选择：不复用 scheduler 的 conn，而是让 C.main 经 DB_PATH 自建连接写库
    ——完整复用 backfill 语义（按目标日覆盖跳过 / 窗口型早停 / 失败断点续跑），
    不重写循环；scheduler 的 conn 仅做读诊断。WAL 下写（collect 连接）与读
    （scheduler 连接）互不阻塞，busy_timeout 兜底；CPython 引用计数会在
    C.main 返回后关闭其临时连接，常驻进程不积压句柄。
    d_to 取昨日而非今天：今日 16:40 例行跑的目标日正是昨日，避免重复 fetch。
    """
    yesterday = C.previous_trade_date(dt.date.today().isoformat(), 1)
    rc = C.main(["--backfill", "--from", d_from, "--to", yesterday,
                 "--source", source["id"]])
    if rc:
        log.warning("源 %s 回补未完成（断点续跑：下次启动/例行后会自动重试）",
                    source["id"])
    return rc


def catchup_if_stale(conn, today: dt.date) -> int:
    """对落后的启用源逐个跑 backfill（复用幂等 upsert）；返回处理的源数。

    触发保守：仅 source_max_date < 昨日交易日 才补。正常情况昨日 16:40 例行跑
    已把源推到前日目标日，此时回补区间内的目标日均已覆盖、逐日跳过，近乎零开销
    ——真正有产出的是"昨日例行失败/调度器停了几天"的场景。
    """
    stale = _stale_sources(conn, today)
    for s, max_d, d_from, horizon in stale:
        log.info("源 %s 落后（最新 %s < %s），从 %s 起回补",
                 s["id"], max_d, horizon, d_from)
        _run_catchup_backfill(conn, s, d_from)
    return len(stale)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    conn = connect()
    init_db(conn)
    n = catchup_if_stale(conn, dt.date.today())
    if n:
        log.info("启动补跑：%d 个源已追平", n)
    while True:
        nxt = next_run(dt.datetime.now())
        log.info("下次采集：%s", nxt)
        time.sleep(max((nxt - dt.datetime.now()).total_seconds(), 1))
        log.info("触发例行采集")
        try:
            C.main([])   # 默认 --date 今天（目标日=前一交易日）
        except Exception:
            log.exception("例行采集异常，下个周期继续")
        # 例行跑后立即再查补跑（防当天早停漏日）
        try:
            catchup_if_stale(conn, dt.date.today())
        except Exception:
            log.exception("例行后补查异常")


if __name__ == "__main__":
    main()
