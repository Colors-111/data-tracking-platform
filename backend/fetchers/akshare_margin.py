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

    # 交易所直连列映射（直连单位为元，比对时统一换算到入库单位亿元再比）
    SSE_COLS = {"financing": "融资余额", "securities": "融券余量金额",
                "total": "融资融券余额", "buying": "融资买入额"}
    SZ_COLS = {"financing": "融资余额", "securities": "融券余额",
               "total": "融资融券余额", "buying": "融资买入额"}
    CHECK_TOLERANCE = 0.005
    # 兼作节流阈值与 SSE 回看窗口（有意合并：窗口 > 节流期则近期目标日永不缺数据）
    CHECK_RECENT_DAYS = 30
    YI = 1e8   # 直连元 → 入库亿元

    def _cross_check(self, trade_date: str, obs: list[Observation]) -> None:
        """交易所直连交叉校验（只告警不落库）：近期目标日尽力比对。
           远古目标日静默跳过（backfill 2010 起逐日校验需数千请求，不划算——
           交易所与乐咕同源转发，近期段持续校验已覆盖设计意图）。"""
        try:
            target = dt.date.fromisoformat(trade_date)
        except ValueError:
            log.info("两融校验跳过（非法目标日）: %s", trade_date)
            return
        if (_today() - target).days > self.CHECK_RECENT_DAYS:
            return
        # 只取目标日当天的沪深产出（fetch 全历史返回，远古行不参与）；
        # 复用 self._lg 映射，免去从 instrument_id 二次解析
        val = {(o.instrument_id, o.trade_date): o.value for o in obs}
        mine = {}
        for exch, targets in self._lg.items():
            for sfx, iid in targets.items():
                v = val.get((iid, trade_date))
                if v is not None:
                    mine[(exch, LG_META[sfx][2])] = v
        if not mine:          # 沪深均未配置/无当日产出 → 无可比对
            return
        self._check_sse(trade_date, mine)
        self._check_szse(trade_date, mine)

    def _check_sse(self, trade_date: str, mine: dict) -> None:
        try:
            start = (dt.date.fromisoformat(trade_date)
                     - dt.timedelta(days=self.CHECK_RECENT_DAYS)).strftime("%Y%m%d")
            df = ak.stock_margin_sse(start_date=start,
                                     end_date=trade_date.replace("-", ""))
        except Exception as e:    # 断连/限流/上游解析异常（如空数据 ValueError）→ 辅助通道静默
            log.info("两融校验跳过（SSE 直连失败）: %s", e)
            return
        if df is None or df.empty:
            log.info("两融校验跳过（SSE 尚无 %s 数据）", trade_date)
            return
        try:                      # 只有我们自己的契约校验才算结构变更
            c_d = col(df, ["信用交易日期"], "日期")
            for cname in self.SSE_COLS.values():
                col(df, [cname], cname)
        except ValueError as e:   # 列结构变更必须可见（否则永久失去校验能力无人知）
            log.warning("两融校验通道结构变更（SSE）: %s", e)
            return
        row = next((r for _, r in df.iterrows()
                    if normalize_date(r[c_d]) == trade_date), None)
        if row is None:
            log.info("两融校验跳过（SSE 尚无 %s 数据）", trade_date)
            return
        self._compare("SH", self.SSE_COLS, row, mine, trade_date)

    def _check_szse(self, trade_date: str, mine: dict) -> None:
        try:
            df = ak.stock_margin_szse(date=trade_date.replace("-", ""))
        except Exception as e:    # 同上：上游异常静默
            log.info("两融校验跳过（SZSE 直连失败）: %s", e)
            return
        if df is None or df.empty:
            log.info("两融校验跳过（SZSE 尚无 %s 数据）", trade_date)
            return
        try:                      # 自有契约校验（列缺失才是结构变更）
            for cname in self.SZ_COLS.values():
                col(df, [cname], cname)
        except ValueError as e:
            log.warning("两融校验通道结构变更（SZSE）: %s", e)
            return
        row = df.iloc[0]
        self._compare("SZ", self.SZ_COLS, row, mine, trade_date)

    def _compare(self, exch: str, cols: dict, row, mine: dict,
                 trade_date: str) -> None:
        """逐指标比对：mine 为入库亿元，直连为元 → 统一换算亿元后算相对差。"""
        for metric, cname in cols.items():
            base = mine.get((exch, metric))
            theirs = row.get(cname)
            if base is None or theirs is None or pd.isna(theirs):
                continue
            try:
                theirs_yi = float(theirs) / self.YI
            except (TypeError, ValueError):   # 脏值：辅助校验不拖垮主采集
                continue
            if base == 0:   # 节流窗口内不可达（近期余额非零），仅防御
                continue
            if abs(theirs_yi - base) / abs(base) > self.CHECK_TOLERANCE:
                log.warning("两融交叉校验不一致: %s %s %s 乐咕=%.2f 交易所=%.2f",
                            exch, trade_date, metric, base, theirs_yi)
