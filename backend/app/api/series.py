# Task 16 实装，先占位通过导入
from fastapi import APIRouter, Depends, Query

from ..db import get_conn

router = APIRouter()


@router.get("/api/series")
def series(instruments: str = Query(""), normalize: int = 0,
           range: str = Query("6m"), conn=Depends(get_conn)):
    return {"as_of_date": None, "series": []}  # Task 16 实装
