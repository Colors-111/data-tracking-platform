"""电解铜库存：futures_inventory_em(symbol='cu') 东财口径，返回近约 60 个交易日全量。
   关键约束：每次抓取把整个返回区间全部 upsert（接口只保留 60 日，历史须自建累积），
   故 fetch 忽略入参 trade_date，全量返回。
   Task 4 实测列：['日期','库存','增减']；fixture 中日期经 to_json 为 epoch-ms int，
   真实接口可能返回字符串/Timestamp——三种形态都要归一成 'YYYY-MM-DD'。"""
import datetime as dt

import akshare as ak
import numpy as np
import pandas as pd

from .base import Observation, register

DATE_CANDIDATES = ["日期", "date", "报告日期"]
VAL_CANDIDATES = ["库存", "库存量", "inventory", "电解铜库存"]

def _col(df, candidates, what):
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(f"未找到{what}列，实际列: {list(df.columns)}")

def _norm_date(v) -> str | None:
    if pd.isna(v):   # NaN/NaT 日期：返回 None 让调用方跳过该行，而非整个抓取崩掉
        return None
    # np.integer 单列出来：iterrows 行内 dtype 混合时 int64 常升位 float64（isinstance float 可命中），
    # 但纯整型行会保持 np.int64，它不是 Python int 的子类
    if isinstance(v, (int, float, np.integer, np.floating)):
        # epoch-ms 分支锚定 UTC：pandas to_json 的 epoch-ms 以 UTC 午夜为基准，
        # 只有 UTC 转换才能无损还原日期；按北京午夜解释会差一天
        return dt.datetime.fromtimestamp(int(v) / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")
    return str(v)[:10].replace("/", "-")   # '2026-07-07' / '2026/07/07' / Timestamp

@register("akshare.futures_inventory_em")
class FuturesInventoryFetcher:
    source_id = "cu-inventory"
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        # instrument_id -> em_symbol（接口每次只收一个品种，如 'cu'）
        self.targets = {i["id"]: i["code"] for i in instruments if i["code"]}

    def fetch(self, trade_date: str) -> list[Observation]:
        out = []
        for iid, symbol in self.targets.items():
            df = ak.futures_inventory_em(symbol=symbol)
            if df is None or df.empty:   # 空表短路：接口波动须返回 [] 而非报错
                continue
            c_d = _col(df, DATE_CANDIDATES, "日期")
            c_v = _col(df, VAL_CANDIDATES, "库存")
            for _, r in df.iterrows():
                v = r[c_v]
                if pd.isna(v):
                    continue
                d = _norm_date(r[c_d])
                if d is None:   # NaT/NaN 日期行：跳过而非崩掉整个抓取
                    continue
                out.append(Observation(iid, "inventory", d, float(v)))
        return out
