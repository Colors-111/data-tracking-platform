from fastapi import APIRouter, Depends, Query

from ..db import get_conn

router = APIRouter()
RANGE_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "all": None}


def _resolve_ids(conn, ids: list[str]) -> list[str]:
    """入参同时支持 instrument id 与 source id（source 自动展开为该源全部 instruments）。"""
    out = []
    for i in ids:
        if conn.execute("SELECT 1 FROM instruments WHERE id=?", (i,)).fetchone():
            out.append(i)
        else:
            out += [r["id"] for r in conn.execute(
                "SELECT id FROM instruments WHERE source_id=?", (i,)).fetchall()]
    return out


@router.get("/api/series")
def series(instruments: str = Query(""), normalize: int = 0,
           range: str = Query("6m"), conn=Depends(get_conn)):
    ids = _resolve_ids(conn, [i for i in instruments.split(",") if i])
    if not ids:
        return {"as_of_date": None, "series": []}
    days = RANGE_DAYS.get(range)
    ph = ",".join("?" * len(ids))
    as_of = conn.execute(
        f"SELECT MAX(trade_date) m FROM observations WHERE instrument_id IN ({ph})",
        ids).fetchone()["m"]
    if not as_of:
        return {"as_of_date": None, "series": []}
    # 注意：as_of 源自上游外部数据（trade_date 的 MAX），必须绑参不得 f-string 拼接
    where_days = f"AND trade_date >= date(?, '-{days} days')" if days else ""
    params = [*ids, as_of] if days else ids
    if normalize:
        sql = f"""SELECT instrument_id, trade_date, value,
                         FIRST_VALUE(value) OVER (PARTITION BY instrument_id ORDER BY trade_date) base
                    FROM observations
                   WHERE instrument_id IN ({ph}) AND value IS NOT NULL {where_days}"""
        raw = conn.execute(sql, params).fetchall()
        grouped: dict[str, list] = {}
        for r in raw:
            if not r["base"]:
                continue
            grouped.setdefault(r["instrument_id"], []).append(
                [r["trade_date"], round(r["value"] / r["base"] * 100, 2)])
    else:
        raw = conn.execute(
            f"""SELECT instrument_id, trade_date, value FROM observations
                 WHERE instrument_id IN ({ph}) {where_days} ORDER BY trade_date""",
            params).fetchall()
        grouped = {}
        for r in raw:
            grouped.setdefault(r["instrument_id"], []).append([r["trade_date"], r["value"]])
    names = {r["id"]: r["name"] for r in conn.execute(
        f"SELECT id, name FROM instruments WHERE id IN ({ph})", ids).fetchall()}
    return {"as_of_date": as_of,
            "series": [{"instrument_id": iid, "name": names.get(iid, iid), "points": pts}
                       for iid, pts in grouped.items()]}
