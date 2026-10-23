"""自定义起止日期校验（/api/sources/{sid} 与 /api/series 共享）。

start/end 是外部用户输入：本模块只做形状校验，通过校验的值由调用方
绑参进 SQL（铁律：外部值一律绑参，不得 f-string 内插）。
"""
import re
from datetime import datetime

from fastapi import HTTPException

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_custom_window(start, end):
    """校验自定义起止（可任缺其一）：格式须 YYYY-MM-DD 且 start ≤ end，否则 400。"""
    parsed = []
    for v in (start, end):
        if v is None:
            continue
        if not DATE_RE.match(v):
            raise HTTPException(400, "bad date, expect YYYY-MM-DD")
        try:
            parsed.append(datetime.strptime(v, "%Y-%m-%d"))
        except ValueError:   # 过正则但非真实日期（如 2026-13-45）：宁严勿漏
            raise HTTPException(400, "bad date, expect YYYY-MM-DD")
    if len(parsed) == 2 and parsed[0] > parsed[1]:
        raise HTTPException(400, "start after end")
