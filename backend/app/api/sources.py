from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_conn

router = APIRouter()
RANGE_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "all": None}


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
def source_detail(sid: str, range: str = Query("3m"), conn=Depends(get_conn)):
    src = conn.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
    if not src:
        raise HTTPException(404, "source not found")
    days = RANGE_DAYS.get(range)
    if days is None and range != "all":
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
    # days 来自 RANGE_DAYS 白名单 int，可内插
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
    return {"id": sid, "name": src["name"], "unit": src["unit"],
            "as_of_date": as_of, "cards": cards, "series": series,
            "instrument_meta": instrument_meta}
