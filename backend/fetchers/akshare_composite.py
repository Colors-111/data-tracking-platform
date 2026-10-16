"""商品复合 fetcher：一个 fetcher 服务四个商品组源（铜/铝/氧化铝/黄金，seed 均
   配 fetcher='akshare.composite'），按 instrument code 约定内部分流三支接口后
   合并返回（源级原子：任一分支异常整体抛出，对齐 etf_scale 混合所语义）：

   - code 'CU0'/'AL0'/'AO0'/'AU0'   → SHFE 主力连续日K（全历史型）：
     futures_zh_daily_sina(symbol) 每次返回整段历史，fetch 忽略入参 trade_date
     逐日全量 upsert（像 cu 库存全量模式），取 close 收盘价为 'price'
   - code 'CU'/'AL'/'AO'            → 生意社现货价（单日型）：
     futures_spot_price(date, vars_list) 一次调用带本源全部品种，按 symbol 过滤，
     取 spot_price 为 'price'。实测 100ppi 已下架氧化铝（vars 带 AO 亦无行），
     缺品种按未注册成分语义跳过，恢复后自动接入
   - code 'CU-WARRANT'/…'-WARRANT'  → 上期所仓单（单日型）：
     futures_shfe_warehouse_receipt(date) 整 dict 只拉一次，按品种明细行求和：
     值为 DataFrame（仓库明细），列 [VARNAME, REGNAME, WHABBRNAME, WRTWGHTS,
     WRTCHANGE, ROWSTATUS]，ROWSTATUS 实测为字符串（'0'明细/'1'区域合计/'2'总计）。
     求和口径取明细行（'0'）——与交易所"总计"行核对一致（fixture 测试锁定），
     且黄金无总计行（单条明细即全量）、铝区域合计行之和 ≠ 总计（部分仓库无分区
     小计），明细求和是唯一对全部品种一致的正确口径。氧化铝 = 仓库 + 厂库两帧合计。
     单位：铜/铝/氧化铝=吨、黄金=千克（单位语义由 seed instruments.unit 承载，
     fetcher 透传原值）

   metric 优先取 instruments 行配置（NOT NULL），防御性回退分支默认：
   日K/现货 → 'price'，仓单 → 'warehouse'。lag_days=1。"""
import akshare as ak
import pandas as pd

from ._util import col, normalize_date
from .base import Observation, register

# code → 分支约定（同源内 code 唯一）
SHFE_DAILY_CODES = ("CU0", "AL0", "AO0", "AU0")       # 主力连续（新浪全历史日K）
SPOT_VARS = ("CU", "AL", "AO")                         # 生意社现货品种
# 仓单 dict 键（上期所原始 VARNAME 分组）；氧化铝 = 仓库 + 厂库 两帧
WARRANT_KEYS = {"CU": ("铜",), "AL": ("铝",),
                "AO": ("氧化铝仓库", "氧化铝厂库"), "AU": ("黄金",)}

DAILY_DATE_CANDIDATES = ["date", "日期"]
DAILY_CLOSE_CANDIDATES = ["close", "收盘价"]
SPOT_SYMBOL_CANDIDATES = ["symbol", "品种"]
SPOT_PRICE_CANDIDATES = ["spot_price", "sp", "现货价格"]
SPOT_DATE_CANDIDATES = ["date", "日期"]
WARRANT_WEIGHT_CANDIDATES = ["WRTWGHTS", "仓单量"]
WARRANT_STATUS_CANDIDATES = ["ROWSTATUS"]


def warrant_total(df) -> float:
    """单帧仓单合计 = 明细行（ROWSTATUS=0）WRTWGHTS 求和（NaN 权重行跳过）。
       详见模块 docstring 的口径论证；ROWSTATUS 数字/字符串形态均兼容。"""
    c_w = col(df, WARRANT_WEIGHT_CANDIDATES, "仓单量")
    c_s = col(df, WARRANT_STATUS_CANDIDATES, "行状态")
    rs = pd.to_numeric(df[c_s], errors="coerce")
    vals = pd.to_numeric(df.loc[rs == 0, c_w], errors="coerce")
    return float(vals.dropna().sum())


@register("akshare.composite")
class CommodityCompositeFetcher:
    # 复合 fetcher 服务多个 source（copper/aluminum/alumina/gold），以 source 字典为准
    source_id = "commodity"
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        # code -> (instrument_id, metric)（只跟踪注册成分；Row 无 .get，schema 保证 metric 非空）
        self.targets = {i["code"]: (i["id"], i["metric"]) for i in instruments if i["code"]}

    def fetch(self, trade_date: str) -> list[Observation]:
        d = trade_date.replace("-", "")
        daily = {c: t for c, t in self.targets.items() if c in SHFE_DAILY_CODES}
        spot = {c: t for c, t in self.targets.items() if c in SPOT_VARS}
        warrant = {c.rsplit("-", 1)[0]: t for c, t in self.targets.items()
                   if c.endswith("-WARRANT")}
        # 分流短路：无成分的分支不触发对应接口（对齐 etf_scale 混合所语义）
        out = self._fetch_daily(daily) if daily else []
        out += self._fetch_spot(trade_date, d, spot) if spot else []
        out += self._fetch_warrants(trade_date, d, warrant) if warrant else []
        return out

    def _fetch_daily(self, codes: dict) -> list[Observation]:
        out = []
        for code, (iid, metric) in codes.items():
            df = ak.futures_zh_daily_sina(symbol=code)
            if df is None or df.empty:   # 空表短路：接口波动返回 [] 而非报错（cu 口径）
                continue
            c_d = col(df, DAILY_DATE_CANDIDATES, "日期")
            c_v = col(df, DAILY_CLOSE_CANDIDATES, "收盘价")
            for _, r in df.iterrows():
                v = r[c_v]
                if pd.isna(v):
                    continue
                day = normalize_date(r[c_d])   # NaN/坏值 → None → 跳过该行
                if day is None:
                    continue
                out.append(Observation(iid, metric or "price", day, float(v)))
        return out

    def _fetch_spot(self, trade_date, d: str, codes: dict) -> list[Observation]:
        df = ak.futures_spot_price(date=d, vars_list=sorted(codes))
        if df is None or df.empty:
            return []
        c_sym = col(df, SPOT_SYMBOL_CANDIDATES, "品种")
        c_v = col(df, SPOT_PRICE_CANDIDATES, "现货价")
        c_d = col(df, SPOT_DATE_CANDIDATES, "日期")
        out = []
        for _, r in df.iterrows():
            target = codes.get(str(r[c_sym]).strip().upper())
            if target is None:   # 未注册/请求外品种（如 100ppi 缺 AO）→ 跳过
                continue
            v = r[c_v]
            if pd.isna(v):
                continue
            out.append(Observation(target[0], target[1] or "price",
                                   normalize_date(r[c_d], fallback=trade_date),
                                   float(v)))
        return out

    def _fetch_warrants(self, trade_date, d: str, codes: dict) -> list[Observation]:
        data = ak.futures_shfe_warehouse_receipt(date=d)
        if not data:
            return []
        out = []
        for sym, (iid, metric) in codes.items():
            total, seen = 0.0, False
            for key in WARRANT_KEYS.get(sym, ()):
                df = data.get(key)
                if df is None or (hasattr(df, "empty") and df.empty):
                    continue   # 单帧缺失：部分汇总不炸；全帧缺失则不产观测（不编 0）
                seen = True
                total += warrant_total(df)
            if seen:
                out.append(Observation(iid, metric or "warehouse", trade_date, total))
        return out
