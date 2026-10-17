from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_conn
from ..validate import check_custom_window

router = APIRouter()
RANGE_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "3y": 1096, "5y": 1827, "all": None}

# v1.3.x ETF 类别总量曲线：源 id → 该类别在全市场快照（etf_snapshots.name）中的
# 成员名正则。口径为"全类"（含未跟踪品种），定义上 100% 覆盖——差值即未跟踪部分。
# 排除口径（实测 2026-10-17）：
#  - csi500：排除标普500（名字含"500"但为美股指数）与 A500（独立类别）
#  - sse50：创业板50/双创50/科创创业50 均为不同指数，用"含排除词整体不匹配"而非
#    定宽 lookbehind（后者对截断名是 no-op）
#  - 全类通用：排除增强/联接
CAT_PATTERNS = {
    'hs300-etf':  r'沪深300.*|300ETF$',
    'csi500-etf': r'中证500.*|500ETF',
    'star50-etf': r'科创50.*|科创板50.*',
    'chinext-etf': r'创业板.*',
    'dividend-etf': r'红利.*|股息.*',
    'a500-etf': r'A500.*',
    'sse50-etf': r'上证50.*|50ETF$|SZ50ETF$',   # 锚定：50ETF 需全名精确（A50/创50/电池50 等主题品种不算）
    'zz1000-etf': r'中证1000.*|1000ETF.*|1000基金.*',
}
# 类别排除词（name 含任一即剔除）：与 CAT_PATTERNS 配对使用
CAT_EXCLUDES = {
    'hs300-etf':  r'增强|联接',
    'csi500-etf': r'增强|联接|标普|A500',
    'star50-etf': r'增强|联接',
    'chinext-etf': r'增强|联接',
    'dividend-etf': r'增强|联接',
    'a500-etf': r'增强|联接',
    'sse50-etf': r'增强|联接|创业板50|双创50|科创创业50|中证1000|AH',
    'zz1000-etf': r'增强|联接',
}


@router.get("/api/sources")
def list_sources(conn=Depends(get_conn)):
    # total_value/day_change：各 instrument 的 rn=1 最新行求和/求差；
    # 跨 instrument 最新日期不齐（各取各的最后一行）时合计值为近似；
    # v1.2：unit='混合' 的商品组源跨量纲（元/克+美元/盎司+吨+张+手）求和无意义 → 置 NULL，
    # 速览卡前端显示"数据见详情"，单一位 ETF 源照旧求和
    rows = conn.execute(
        """WITH latest AS (
             SELECT i.source_id, o.value,
                    LAG(o.value) OVER (PARTITION BY o.instrument_id ORDER BY o.trade_date) prev_v,
                    ROW_NUMBER() OVER (PARTITION BY o.instrument_id ORDER BY o.trade_date DESC) rn
               FROM observations o JOIN instruments i ON i.id=o.instrument_id)
           SELECT s.id, s.name, s.group_name, s.unit, s.sort_order,
                  (SELECT COUNT(*) FROM instruments i WHERE i.source_id=s.id) AS inst_count,
                  (SELECT MAX(o.trade_date) FROM observations o
                     JOIN instruments i2 ON i2.id=o.instrument_id WHERE i2.source_id=s.id) AS latest_date,
                  CASE WHEN s.unit='混合' THEN NULL ELSE
                    (SELECT SUM(value) FROM latest WHERE latest.source_id=s.id AND rn=1) END AS total_value,
                  CASE WHEN s.unit='混合' THEN NULL ELSE
                    (SELECT SUM(value-prev_v) FROM latest
                       WHERE latest.source_id=s.id AND rn=1 AND prev_v IS NOT NULL) END AS day_change
             FROM sources s WHERE s.enabled=1 ORDER BY s.sort_order""").fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["group_name"], []).append(dict(r))
    return [{"group": g, "sources": ss} for g, ss in groups.items()]


@router.get("/api/sources/{sid}")
def source_detail(sid: str, range: str = Query("3m"),
                  start: str = Query(None), end: str = Query(None),
                  conn=Depends(get_conn)):
    check_custom_window(start, end)
    src = conn.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not src:
        raise HTTPException(404, "source not found")
    days = RANGE_DAYS.get(range)
    # 口径与 /api/series 统一：给了自定义起止时窗口完全由 start/end 决定，
    # range 档位被忽略、不再做白名单校验（range=banana + start 仍是合法请求）
    if days is None and range != "all" and start is None and end is None:
        raise HTTPException(400, "bad range")
    insts = conn.execute(
        "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
        (sid,)).fetchall()
    # v1.2 商品组源：全量 instruments 的指标/单位元数据（含无数据的，供前端分区展示）；
    # unit 统一在此回退（instrument.unit 为空回退 source.unit），前端直接用
    instrument_meta = {i["id"]: {"metric": i["metric"], "unit": i["unit"] or src["unit"],
                                 "name": i["name"]} for i in insts}
    as_of = conn.execute(
        """SELECT MAX(o.trade_date) m FROM observations o
             JOIN instruments i ON i.id=o.instrument_id WHERE i.source_id=?""",
        (sid,)).fetchone()["m"]
    if not as_of:
        return {"id": sid, "name": src["name"], "unit": src["unit"],
                "as_of_date": None, "cards": [], "series": {},
                "instrument_meta": instrument_meta}
    # as_of 源自库内 trade_date（上游外部数据），必须绑参——防上游构造词典序居大的注入串；
    # days 来自 RANGE_DAYS 白名单 int，可内插。
    # 窗口语义：给了 start/end 任一即覆盖 range 档位（range 与 start 同给 → start 优先）；
    # start/end 为用户输入必须绑参；end 缺省 = as_of——查询天然只含 ≤ MAX(trade_date) 的行，无需上界
    if start is not None or end is not None:
        conds = []
        if start is not None:
            conds.append("o.trade_date >= ?")
        if end is not None:
            conds.append("o.trade_date <= ?")
        day_filter = "AND " + " AND ".join(conds)
        params = (sid, *[v for v in (start, end) if v is not None])
    else:
        day_filter = f"AND o.trade_date >= date(?, '-{days} days')" if days else ""
        params = (sid, as_of) if days else (sid,)
    rows = conn.execute(
        f"""SELECT o.instrument_id, o.trade_date, o.value,
                   FIRST_VALUE(o.value) OVER w AS first_v,
                   LAG(o.value)       OVER w AS prev_v,
                   ROW_NUMBER() OVER w2      AS rn_desc
              FROM observations o
              JOIN instruments i ON i.id=o.instrument_id
             WHERE i.source_id=? {day_filter}
             WINDOW w AS (PARTITION BY o.instrument_id ORDER BY o.trade_date),
                    w2 AS (PARTITION BY o.instrument_id ORDER BY o.trade_date DESC)
             ORDER BY o.instrument_id, o.trade_date""",
        params).fetchall()
    cards, series = [], {}
    by_inst: dict[str, list] = {}
    for r in rows:
        by_inst.setdefault(r["instrument_id"], []).append(r)
    for inst in insts:
        rs = by_inst.get(inst["id"], [])
        pts = [(r["trade_date"], r["value"]) for r in rs]  # 已按日期升序，series 可直接画线
        series[inst["id"]] = pts
        if rs:
            last = next(r for r in rs if r["rn_desc"] == 1)
            card = {"instrument_id": inst["id"], "name": inst["name"],
                    "code": inst["code"], "metric": inst["metric"],
                    "unit": inst["unit"] or src["unit"],
                    "value": last["value"],
                    "day_change": None, "day_pct": None,
                    "range_change": None, "range_pct": None}
            if last["prev_v"] is not None and last["prev_v"] != 0:
                card["day_change"] = round(last["value"] - last["prev_v"], 4)
                card["day_pct"] = round((last["value"] / last["prev_v"] - 1) * 100, 2)
            if last["first_v"] not in (None, 0) and last["first_v"] != last["value"]:
                card["range_change"] = round(last["value"] - last["first_v"], 4)
                card["range_pct"] = round((last["value"] / last["first_v"] - 1) * 100, 2)
            cards.append(card)
    resp = {"id": sid, "name": src["name"], "unit": src["unit"],
            "as_of_date": as_of, "cards": cards, "series": series,
            "instrument_meta": instrument_meta}
    # v1.3.x 类别总量曲线（仅指数 ETF 组、非混合单位、有类别口径的源）：
    #   category_total——全市场口径，etf_snapshots 按类别名正则聚合按日求和（窗口同主查询）；
    #   tracked_total——本源 instruments 观测按日合计（窗口同主查询）；
    #   coverage——最新类别日 tracked/category*100（一位小数）；类别无数据 → None，
    #   该日本源无观测 → 0.0（诚实显示 0 而非虚高）。
    # 正则经 REGEXP(?, name) 在库内过滤聚合（连接级 create_function，见 db.connect），
    # 避免把整表快照拉进 Python（all 窗口下 150 万行）。
    pattern = (CAT_PATTERNS.get(sid)
               if src["group_name"] == "指数 ETF" and src["unit"] != "混合" else None)
    if pattern:
        # 排除词编入负向先行（REGEXP 注册的是 re.search，完整 Python 正则语义可用）：
        # "任意位置不含排除词" 且 "整体匹配类别模式"——CAT_PATTERNS 分支自带锚定
        # （.* 前缀/后缀由各分支自负责，否则 XX50ETF 主题品种会借尾部匹配混入）
        exclude = CAT_EXCLUDES.get(sid, "")
        pattern = (f"^(?!.*(?:{exclude}))(?:{pattern})$" if exclude
                   else f"^(?:{pattern})$")
        tracked: dict[str, float] = {}
        for r in rows:
            if r["value"] is not None:
                tracked[r["trade_date"]] = tracked.get(r["trade_date"], 0.0) + r["value"]
        resp["tracked_total"] = sorted(tracked.items())
        if start is not None or end is not None:
            cat_conds = []
            cat_params = [pattern]
            if start is not None:
                cat_conds.append("trade_date >= ?")
                cat_params.append(start)
            if end is not None:
                cat_conds.append("trade_date <= ?")
                cat_params.append(end)
            cat_filter = " AND " + " AND ".join(cat_conds)
        else:
            cat_filter = (" AND trade_date >= date(?, '-%d days')" % days) if days else ""
            cat_params = [pattern, as_of] if days else [pattern]
        cat_rows = conn.execute(
            f"""SELECT trade_date, SUM(shares) s FROM etf_snapshots
                 WHERE REGEXP(?, name){cat_filter}
                 GROUP BY trade_date ORDER BY trade_date""", cat_params).fetchall()
        category_total = [[r["trade_date"], r["s"]] for r in cat_rows]
        resp["category_total"] = category_total
        coverage = None
        if category_total and category_total[-1][1]:
            last_date, cat_v = category_total[-1]
            coverage = round(tracked.get(last_date, 0.0) / cat_v * 100, 1)
        resp["coverage"] = coverage
    return resp
