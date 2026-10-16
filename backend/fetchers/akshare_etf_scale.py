"""混合交易所 ETF 份额 fetcher：按代码前缀分流。
   5x/6x（上交所）→ fund_etf_scale_sse(date) 当日全所快照过滤；
   15x（深交所）→ fund_scale_daily_szse(start, end, symbol='ETF') 单日区间。
   两所接口单位均为"份"，入库统一亿份（÷1e8）。T+1 公布 → lag_days=1。"""
import akshare as ak
from ._util import (CODE_CANDIDATES, SHARE_CANDIDATES, clean_name, col,
                    norm_code, normalize_date)
from .base import Observation, register

@register("akshare.etf_scale")
class EtfScaleFetcher:
    # 单 fetcher 服务多个 source（csi500/star50/chinext/dividend-etf），以 source 字典为准
    source_id = "etf-scale"
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        # code -> instrument_id（只跟踪注册的成分基金）
        self.targets = {i["code"]: i["id"] for i in instruments if i["code"]}

    def fetch(self, trade_date: str) -> list[Observation]:
        d = trade_date.replace("-", "")
        sse_codes = {c: i for c, i in self.targets.items() if not c.startswith("1")}
        szse_codes = {c: i for c, i in self.targets.items() if c.startswith("1")}
        out = self._fetch_sse(trade_date, d, sse_codes) if sse_codes else []
        out += self._fetch_szse(trade_date, d, szse_codes) if szse_codes else []
        return out

    def _fetch_sse(self, trade_date: str, d: str, codes: dict) -> list[Observation]:
        df = ak.fund_etf_scale_sse(d)
        if df is None or df.empty:
            return []
        c_code = col(df, CODE_CANDIDATES, "代码")
        c_share = col(df, SHARE_CANDIDATES, "份额")
        out = []
        for _, r in df.iterrows():
            code = norm_code(r[c_code])
            if code in codes:
                out.append(Observation(codes[code], "share", trade_date,
                                       float(r[c_share]) / 1e8,   # 份 → 亿份
                                       {"name": clean_name(r.get("基金简称"))}))
        return out

    def _fetch_szse(self, trade_date: str, d: str, codes: dict) -> list[Observation]:
        # 单日区间 start==end：返回行必然属于请求日；日期列仅做核验归一
        df = ak.fund_scale_daily_szse(start_date=d, end_date=d, symbol="ETF")
        if df is None or df.empty:
            return []
        c_code = col(df, CODE_CANDIDATES, "代码")
        c_share = col(df, SHARE_CANDIDATES, "份额")
        out = []
        for _, r in df.iterrows():
            code = norm_code(r[c_code])
            if code in codes:
                out.append(Observation(codes[code], "share",
                                       normalize_date(r.get("日期"), fallback=trade_date),
                                       float(r[c_share]) / 1e8,   # 份 → 亿份
                                       {"name": clean_name(r.get("基金简称"))}))
        return out
