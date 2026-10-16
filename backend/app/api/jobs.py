# 占位，Task 12 实装
from fastapi import APIRouter, Depends

from ..db import get_conn

router = APIRouter()


@router.get("/api/jobs/latest")
def jobs_latest(conn=Depends(get_conn)):
    return {"jobs": []}
