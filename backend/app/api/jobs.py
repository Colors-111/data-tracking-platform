from fastapi import APIRouter, Depends

from ..db import get_conn

router = APIRouter()


@router.get("/api/jobs/latest")
def jobs_latest(conn=Depends(get_conn)):
    rows = conn.execute(
        """SELECT j.* FROM job_runs j
             JOIN (SELECT source_id, MAX(id) mid FROM job_runs
                    GROUP BY source_id) t ON t.mid=j.id
             JOIN sources s ON s.id=j.source_id""").fetchall()
    return {"jobs": [dict(r) for r in rows]}
