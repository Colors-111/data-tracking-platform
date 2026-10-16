# 占位，Task 12 实装
from fastapi import APIRouter, Depends, Query

from ..db import get_conn

router = APIRouter()


@router.get("/api/movements")
def movements(date: str = Query(None), conn=Depends(get_conn)):
    return {"as_of_date": None, "movements": []}
