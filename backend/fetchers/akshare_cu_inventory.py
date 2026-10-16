"""电解铜库存：futures_inventory_em(symbol='cu') 东财口径，返回近约 60 个交易日全量。
   关键约束：每次抓取把整个返回区间全部 upsert（接口只保留 60 日，历史须自建累积），
   故 fetch 忽略入参 trade_date，全量返回。
   Task 4 实测列：['日期','库存','增减']；fixture 中日期经 to_json 为 epoch-ms int，
   真实接口可能返回字符串/Timestamp——四种形态由 _util.normalize_date 统一归一。"""
import akshare as ak
import pandas as pd

from ._util import col, normalize_date
from .base import Observation, register

DATE_CANDIDATES = ["日期", "date", "报告日期"]
VAL_CANDIDATES = ["库存", "库存量", "inventory", "电解铜库存"]

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
            c_d = col(df, DATE_CANDIDATES, "日期")
            c_v = col(df, VAL_CANDIDATES, "库存")
            for _, r in df.iterrows():
                v = r[c_v]
                if pd.isna(v):
                    continue
                d = normalize_date(r[c_d])   # NaN → None → 跳过该行，不崩整个抓取
                if d is None:
                    continue
                out.append(Observation(iid, "inventory", d, float(v)))
        return out
