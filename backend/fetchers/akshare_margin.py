"""融资融券源（v1.4 设计 §5.4）：三上游主通道 + 交易所直连交叉校验。

   主通道（入库，全历史一次拉取、幂等 upsert）：
   - 乐咕 macro_china_market_margin_{sh,sz}：沪深分开余额类宽表（2010-03 起，
     当日晚间更新当日；金额单位元 → ÷1e8 入库亿元）
   - 中证金融 stock_margin_account_info：全市场账户统计（2012-09 起；余额/担保物
     单位亿元原样、维持担保比例 % 原样、参与交易投资者数 个 → ÷1e4 万人；
     担保物/投资者数 2012-09-27→2015-04-22 为 NaN，跳行如实缺失）

   校验通道（只告警不落库）：交易所直连 stock_margin_sse（区间）/stock_margin_szse
   （单日）——同日同指标相对差 >0.5% 告警；直连失败/无数据/远古目标日（距运行日
   >30 日历日：backfill 逐日校验需数千请求，近期段持续校验已覆盖设计意图）静默跳过。

   三接口分支级隔离（run_branches）：单接口挂不拖死其余，全部失败才抛；
   ValueError（缺列等契约错误）不隔离（fail-loud）。fetch 忽略 trade_date
   全量返回——backfill 首目标日全量入库后 covered 门控自然续跑（零逐日请求）。"""
import datetime as dt
import logging
from functools import partial

import akshare as ak
import pandas as pd

from ._util import col, normalize_date, run_branches
from .base import Observation, register

log = logging.getLogger(__name__)

# 仪器 code 后缀 → (上游列名, 换算除数, metric)
LG_META = {
    "FINANCING": ("融资余额", 1e8, "financing"),
    "SECURITIES": ("融券余额", 1e8, "securities"),
    "TOTAL": ("融资融券余额", 1e8, "total"),
    "BUYING": ("融资买入额", 1e8, "buying"),
}
ACCT_META = {
    "MAINTENANCE": ("平均维持担保比例", 1.0, "maintenance"),
    "COLLATERAL": ("担保物总价值", 1.0, "collateral"),
    "INVESTORS": ("参与交易的投资者数量", 1e4, "investors"),
}


def _today() -> dt.date:
    """校验节流的"今天"（独立函数便于测试注入确定日期）。"""
    return dt.date.today()


@register("akshare.margin")
class MarginFetcher:
    source_id = "margin"
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        self._lg = {"SH": {}, "SZ": {}}   # 后缀 → iid（列名/除数/metric 查 LG_META）
        self._acct = {}                    # 后缀 → iid
        for i in instruments:
            code = i["code"] or ""
            parts = code.split("-")        # MARGIN-{SH|SZ}-{后缀} / MARGIN-{后缀}
            if len(parts) == 3 and parts[0] == "MARGIN" and parts[1] in ("SH", "SZ") \
                    and parts[2] in LG_META:
                if parts[2] in self._lg[parts[1]]:
                    raise ValueError(f"margin 仪器代码重复: {code}")
                self._lg[parts[1]][parts[2]] = i["id"]
            elif len(parts) == 2 and parts[0] == "MARGIN" and parts[1] in ACCT_META:
                if parts[1] in self._acct:
                    raise ValueError(f"margin 仪器代码重复: {code}")
                self._acct[parts[1]] = i["id"]
            else:
                raise ValueError(
                    f"margin 未支持的仪器代码: {code}（约定 MARGIN-{{SH|SZ}}-"
                    f"{{FINANCING|SECURITIES|TOTAL|BUYING}} 或 MARGIN-"
                    f"{{MAINTENANCE|COLLATERAL|INVESTORS}}）")

    def fetch(self, trade_date: str) -> list[Observation]:
        branches = []
        if self._lg["SH"]:
            branches.append(("lg_sh", partial(self._fetch_lg, "SH", self._lg["SH"])))
        if self._lg["SZ"]:
            branches.append(("lg_sz", partial(self._fetch_lg, "SZ", self._lg["SZ"])))
        if self._acct:
            branches.append(("account", partial(self._fetch_account, self._acct)))
        obs = run_branches(log, "margin", branches)
        self._cross_check(trade_date, obs)
        return obs

    def _fetch_lg(self, exchange: str, targets: dict) -> list[Observation]:
        if not targets:
            return []
        df = getattr(ak, f"macro_china_market_margin_{exchange.lower()}")()
        return self._wide_to_obs(df, targets, LG_META)

    def _fetch_account(self, targets: dict) -> list[Observation]:
        if not targets:
            return []
        return self._wide_to_obs(ak.stock_margin_account_info(), targets, ACCT_META)

    @staticmethod
    def _wide_to_obs(df, targets: dict, meta: dict) -> list[Observation]:
        """全历史宽表 → 观测：日期列归一、逐行逐仪器取值除换算、NaN 跳行。"""
        if df is None or df.empty:
            return []
        c_d = col(df, ["日期"], "日期")
        resolved = []
        for sfx, iid in targets.items():
            cname, div, metric = meta[sfx]
            resolved.append((iid, col(df, [cname], f"{iid} 值"), div, metric))
        out = []
        for _, r in df.iterrows():
            d = normalize_date(r[c_d])
            if d is None:
                continue
            for iid, c_v, div, metric in resolved:
                v = r[c_v]
                if not pd.isna(v):
                    out.append(Observation(iid, metric, d, float(v) / div))
        return out

    def _cross_check(self, trade_date: str, obs: list[Observation]) -> None:
        """交叉校验占位（Task 5 实现，本任务空转保证 fetch 契约完整）。"""
