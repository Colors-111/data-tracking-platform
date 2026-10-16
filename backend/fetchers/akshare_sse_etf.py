"""上交所 ETF 份额：fund_etf_scale_sse(date) 返回当日全所 ETF 快照，历史可回溯 2015。
   T+1 公布 → lag_days=1。
   Task 4 实测列：['序号','基金代码','基金简称','ETF类型','统计日期','基金份额']；
   基金份额单位为"份"，入库统一亿份（÷1e8）。"""
import akshare as ak
from .base import Observation, register

CODE_CANDIDATES = ["基金代码", "证券代码", "代码"]
SHARE_CANDIDATES = ["基金份额", "基金份额(亿份)", "份额合计(亿份)", "份额"]

def _col(df, candidates, what):
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(f"未找到{what}列，实际列: {list(df.columns)}")

@register("akshare.sse_etf_scale")
class SseEtfScaleFetcher:
    source_id = "hs300-etf"
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        # code -> instrument_id（只跟踪注册的成分基金）
        self.targets = {i["code"]: i["id"] for i in instruments if i["code"]}

    def fetch(self, trade_date: str) -> list[Observation]:
        df = ak.fund_etf_scale_sse(trade_date.replace("-", ""))
        if df is None or df.empty:
            return []
        c_code = _col(df, CODE_CANDIDATES, "代码")
        c_share = _col(df, SHARE_CANDIDATES, "份额")
        out = []
        for _, r in df.iterrows():
            code = str(r[c_code]).zfill(6)
            if code in self.targets:
                out.append(Observation(
                    self.targets[code], "share", trade_date,
                    float(r[c_share]) / 1e8,   # 份 → 亿份
                    {"name": str(r.get("基金简称", ""))}))
        return out
