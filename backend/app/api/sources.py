from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_conn

router = APIRouter()
RANGE_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "all": None}


@router.get("/api/sources")
def list_sources(conn=Depends(get_conn)):
    rows = conn.execute(
        """SELECT s.id, s.name, s.group_name, s.unit, s.sort_order,
                  (SELECT COUNT(*) FROM instruments i WHERE i.source_id=s.id) AS inst_count,
                  (SELECT MAX(o.trade_date) FROM observations o
                     JOIN instruments i2 ON i2.id=o.instrument_id
                    WHERE i2.source_id=s.id) AS latest_date
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
    as_of = conn.execute(
        """SELECT MAX(o.trade_date) m FROM observations o
             JOIN instruments i ON i.id=o.instrument_id WHERE i.source_id=?""",
        (sid,)).fetchone()["m"]
    if not as_of:
        return {"id": sid, "name": src["name"], "unit": src["unit"],
                "as_of_date": None, "cards": [], "series": {}}
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
    for inst in conn.execute(
            "SELECT * FROM instruments WHERE source_id=? ORDER BY sort_order",
            (sid,)).fetchall():
        rs = by_inst.get(inst["id"], [])
        pts = [(r["trade_date"], r["value"]) for r in rs]  # 已按日期升序，series 可直接画线
        series[inst["id"]] = pts
        if rs:
            last = next(r for r in rs if r["rn_desc"] == 1)
            card = {"instrument_id": inst["id"], "name": inst["name"],
                    "code": inst["code"], "value": last["value"],
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
            "as_of_date": as_of, "cards": cards, "series": series}
