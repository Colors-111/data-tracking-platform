from fastapi import APIRouter, Depends, Query

from ..db import get_conn

router = APIRouter()


@router.get("/api/movements")
def movements(date: str = Query(None), conn=Depends(get_conn)):
    as_of = date or conn.execute("SELECT MAX(trade_date) m FROM observations").fetchone()["m"]
    if not as_of:
        return {"as_of_date": None, "movements": []}
    rows = conn.execute(
        """WITH ranked AS (
             SELECT o.instrument_id, o.trade_date, o.value,
                    LAG(o.value) OVER w AS prev_v,
                    ROW_NUMBER() OVER w2 AS rn
               FROM observations o
              WHERE o.trade_date <= :as_of
             WINDOW w  AS (PARTITION BY o.instrument_id, o.metric ORDER BY o.trade_date),
                    w2 AS (PARTITION BY o.instrument_id, o.metric ORDER BY o.trade_date DESC)
           ), spark AS (
             SELECT instrument_id, trade_date, value,
                    ROW_NUMBER() OVER (PARTITION BY instrument_id ORDER BY trade_date DESC) rn
               FROM observations WHERE trade_date <= :as_of
           )
           SELECT r.instrument_id, r.trade_date, r.value, r.prev_v,
                  i.name AS instrument_name, s.id AS source_id, s.name AS source_name, s.unit,
                  (SELECT group_concat(v) FROM (
                     SELECT CAST(value AS TEXT) v FROM spark
                      WHERE instrument_id=r.instrument_id AND rn<=5 ORDER BY trade_date)) AS spark5
             FROM ranked r
             JOIN instruments i ON i.id=r.instrument_id
             JOIN sources s ON s.id=i.source_id
            WHERE r.rn=1 AND s.enabled=1
              AND r.prev_v IS NOT NULL AND r.prev_v != 0
              AND ABS(r.value/r.prev_v - 1)*100 >= COALESCE(s.movement_th, 1e9)
            ORDER BY ABS(r.value/r.prev_v - 1) DESC""",
        {"as_of": as_of}).fetchall()
    movements = []
    for r in rows:
        pct = (r["value"] / r["prev_v"] - 1) * 100
        movements.append({
            "instrument_id": r["instrument_id"], "instrument_name": r["instrument_name"],
            "source_id": r["source_id"], "source_name": r["source_name"],
            "unit": r["unit"], "trade_date": r["trade_date"], "value": r["value"],
            "change": round(r["value"] - r["prev_v"], 4), "pct": round(pct, 2),
            "spark5": [float(x) for x in (r["spark5"] or "").split(",") if x],
        })
    return {"as_of_date": as_of, "movements": movements}
