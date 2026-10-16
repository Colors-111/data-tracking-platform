"""宏观全历史型 fetcher 批（v1.2 Task C）：LME 库存/仓单、LME 净持仓、
   CFTC 商品非商业净多、央行黄金储备。

   共同语义（与 cu 库存一致的全历史型）：接口单次返回全历史序列、无增量参数，
   fetch 忽略入参 trade_date 全量返回，幂等 upsert；每日例行重复拉全史代价可接受
   （单表 ≤3k 行）。backfill 首日全量入库后按目标日判覆盖 covered=True 自然续跑，
   若回补起点早于接口历史首日则触发窗口型早停（rows>0 且不含目标日）。

   实测记录（2026-10-16，akshare 1.19.x，fixture 同日录制）：
   - macro_euro_lme_stock：2718 行（2014-07-03~2026-07-17），日期 + 镍/铝/锌/铅/锡/铜
     各三列（X-库存/X-注册仓单/X-注销仓单，单位吨）。尾部停在 2026-07-17——金十源
     更新滞后约 3 个月，如实采集，stale 检测天然处理。v1.2 只接铜/铝的库存与注册仓单
     （注销仓单接口有列但未设仪器 code，暂不映射）。
   - macro_euro_lme_holding：756 行（2015-10-22~2021-09-03），日期 + 各品种
     多头/空头/净仓位（单位张）。金十源 2021-09 后停更约 5 年——只取净仓位（铜/铝），
     长期 stale 如实上报。
   - CFTC：任务书预期的 macro_usa_cftc_nc_holding 实测为【外汇】非商业持仓
     （美元/欧元/日元…，无商品列）。商品非商业在 macro_usa_cftc_c_holding
     （dc_cftc_c_report：原油/原糖/大豆/豆油/豆粕/白银/铂金/钯金/天然气/黄金/棉花/
     玉米 12 品种，周频，日期=报告周二、次周周六（北京）发布）。四个 CFTC 接口均
     【无铜品种】——CFTC-CU 无法由 akshare 供给，仪器代码出现即在构建期 fail-loud，
     需直连 CFTC COT 官方报告另行立项。本接口取黄金非商业净多（单位手）。
   - macro_china_foreign_exchange_gold：420 行月频（1978.12~2026.9），统计时间
     'YYYY.M'（str）、黄金储备单位万盎司。接口只给月粒度，归一为当月最后一日
     （月度读数锚定月末，异动/环比语义不变）；'YYYY.10' 等十月标切勿经 float 中转
     （2025.10→2025.1 丢月，测试以 dtype=str 防回归）。

   lag_days 取值（run_source 目标日 = 之前第 n 个交易日，stale 判 m < target）：
   - lme_stock/lme_holding=1（日频隔日更新）；
   - cftc_nc=10：周二日期周频，10 个交易日使周一~周五例行目标日恒 ≤ 最新报告日，
     免除周中假 stale；报告缺发/迟发逾一周才如实报 stale；
   - gold_reserve=28：月末锚定月频、次月约 7 日发布，28 个交易日覆盖发布间隔，
     月读数迟逾约一周才报 stale。

   v1.2 增量（2026-10-16）：SGE 黄金现货（spot_hist_sge('Au99.99')，元/克）与
   伦敦金现（新浪外盘日K futures_foreign_hist('XAU')，美元/盎司）两个全历史
   单序列子 fetcher（SgeSpotFetcher/LondonSpotFetcher），【不注册】——composite
   （编排增量）按仪器 code 'AU9999'/'XAU' import 委托。伦敦金不走东财 forex 系：
   实测 symbol_market_map 仅外汇对（forex_hist_em('XAUUSD') KeyError by design）、
   forex_spot_em 限流 ConnectionError、裸 kline secid 119.XAU/120.XAU 返回 0 根。
   两类与 _WideTableFetcher 同约定：全量返回、幂等 upsert、未知 code 构建期
   fail-loud；fixture 实测记录见 tests/gen_macro_fixtures.py。

   v1.2.1 增量（2026-10-16）：SPDR 黄金 ETF 持仓（SpdrGoldFetcher，
   macro_cons_gold()，总库存单位吨——SPDR GLD 全球最大黄金 ETF，日频，2004 年
   起全历史）。【不注册】——composite 按仪器 code 'SPDR-GLD' import 委托（同
   现货两子类契约，走 _WideTableFetcher）。接口无参数返回全历史宽表，日期列
   生产形态为 datetime.date 对象（normalize_date str 分支兜住）；metric 硬编码
   'holding'，单位语义由 seed instruments.unit（'吨'）承载，fetcher 透传原值。"""
import calendar
import re

import akshare as ak
import pandas as pd

from ._util import col, normalize_date
from .base import Observation, register

DATE_CANDIDATES = ["日期", "date", "报告日期"]

# 金十 LME 宽表的品种列前缀；v1.2 仪器 code 只含铜/铝，余者按需扩
LME_METALS = {"CU": "铜", "AL": "铝"}


class _WideTableFetcher:
    """全历史宽表基类：接口一次返回全历史 DataFrame（日期 + 品种×指标列）。
       子类声明 interface（akshare 接口名）、date_cols/date_parse，并实现
       _build_targets(instruments) -> [(instrument_id, metric, [值列候选])]。
       未知仪器代码在构建期 raise（seed 拼错当场暴露，禁静默丢数据）。"""

    interface: str
    date_cols = DATE_CANDIDATES
    date_parse = staticmethod(normalize_date)
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        self.targets = self._build_targets(instruments)

    def _build_targets(self, instruments):
        raise NotImplementedError

    def fetch(self, trade_date: str) -> list[Observation]:
        df = getattr(ak, self.interface)()
        if df is None or df.empty:      # 空表短路：接口波动须返回 [] 而非报错
            return []
        c_d = col(df, self.date_cols, "日期")
        resolved = [(iid, metric, col(df, cands, f"{iid} 值"))
                    for iid, metric, cands in self.targets]
        out = []
        for _, r in df.iterrows():
            d = self.date_parse(r[c_d])
            if d is None:               # 日期缺失行跳过（NaN/NaT），不崩整个抓取
                continue
            for iid, metric, c_v in resolved:
                v = r[c_v]
                if not pd.isna(v):
                    out.append(Observation(iid, metric, d, float(v)))
        return out


@register("akshare.lme_stock")
class LmeStockFetcher(_WideTableFetcher):
    """LME 库存/注册仓单：LME-{CU|AL}-{STOCK|WARRANT} → {品种}-库存 / {品种}-注册仓单。"""

    source_id = "lme-stock"
    interface = "macro_euro_lme_stock"
    _KINDS = {"STOCK": ("inventory", "库存"), "WARRANT": ("warehouse", "注册仓单")}

    def _build_targets(self, instruments):
        targets = []
        for i in instruments:
            code = i["code"] or ""
            parts = code.split("-")     # 形如 LME-CU-STOCK
            if (len(parts) != 3 or parts[0] != "LME"
                    or parts[1] not in LME_METALS or parts[2] not in self._KINDS):
                raise ValueError(
                    f"lme_stock 未支持的仪器代码: {code}"
                    f"（约定 LME-{{CU|AL}}-{{STOCK|WARRANT}}）")
            metal, (metric, suffix) = LME_METALS[parts[1]], self._KINDS[parts[2]]
            targets.append((i["id"], metric, [f"{metal}-{suffix}"]))
        return targets


@register("akshare.lme_holding")
class LmeHoldingFetcher(_WideTableFetcher):
    """LME 多空持仓净仓位：LME-{CU|AL}-POS → {品种}-净仓位（单位张）。"""

    source_id = "lme-holding"
    interface = "macro_euro_lme_holding"

    def _build_targets(self, instruments):
        targets = []
        for i in instruments:
            code = i["code"] or ""
            parts = code.split("-")     # 形如 LME-CU-POS
            if (len(parts) != 3 or parts[0] != "LME"
                    or parts[1] not in LME_METALS or parts[2] != "POS"):
                raise ValueError(
                    f"lme_holding 未支持的仪器代码: {code}（约定 LME-{{CU|AL}}-POS）")
            targets.append((i["id"], "positioning", [f"{LME_METALS[parts[1]]}-净仓位"]))
        return targets


# 金十 CFTC 商品非商业报告品种（代码后缀 → 列前缀）；白银 AG 等在列品种按需扩
CFTC_COMMODITIES = {"AU": "黄金"}


@register("akshare.cftc_nc")
class CftcNcFetcher(_WideTableFetcher):
    """CFTC 商品非商业净多：CFTC-{AU} → 黄金-净仓位（单位手，周频）。
       注意 akshare 四个 CFTC 接口均无铜品种——CFTC-CU 在此构建期 fail-loud。"""

    source_id = "cftc-nc"
    interface = "macro_usa_cftc_c_holding"
    lag_days = 10

    def _build_targets(self, instruments):
        targets = []
        for i in instruments:
            code = i["code"] or ""
            parts = code.split("-")     # 形如 CFTC-AU
            if (len(parts) != 2 or parts[0] != "CFTC"
                    or parts[1] not in CFTC_COMMODITIES):
                raise ValueError(
                    f"cftc_nc 未支持的仪器代码: {code}（akshare CFTC 商品非商业接口"
                    f"无铜品种列，CFTC-CU 需直连 CFTC COT 官方报告另行立项；"
                    f"已支持: {sorted(CFTC_COMMODITIES)}）")
            targets.append((i["id"], "positioning",
                            [f"{CFTC_COMMODITIES[parts[1]]}-净仓位"]))
        return targets


_MONTH_RE = re.compile(r"^(\d{4})\.(\d{1,2})$")


def _month_end(v):
    """'YYYY.M'（接口原样，月粒度）→ 当月最后一日 'YYYY-MM-DD'；
       NaN/NaT → None（跳行）；格式异常 raise（结构性变更 fail-loud，禁静默跳月）。"""
    if v is None or pd.isna(v):
        return None
    m = _MONTH_RE.match(str(v).strip())
    if not m:
        raise ValueError(f"统计时间格式异常（预期 YYYY.M）: {v!r}")
    y, mo = int(m[1]), int(m[2])
    if not 1 <= mo <= 12:
        raise ValueError(f"统计时间月份越界: {v!r}")
    return f"{y:04d}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"


@register("akshare.gold_reserve")
class GoldReserveFetcher(_WideTableFetcher):
    """央行黄金储备：GOLD-RESERVE → 黄金储备（单位万盎司，月频、锚定月末）。"""

    source_id = "gold-reserve"
    interface = "macro_china_foreign_exchange_gold"
    date_cols = ["统计时间"]
    date_parse = staticmethod(_month_end)
    lag_days = 28

    def _build_targets(self, instruments):
        targets = []
        for i in instruments:
            code = i["code"] or ""
            if code != "GOLD-RESERVE":
                raise ValueError(
                    f"gold_reserve 未支持的仪器代码: {code}（约定 GOLD-RESERVE）")
            targets.append((i["id"], "reserve", ["黄金储备"]))
        return targets


# ---------- 现货子 fetcher（不注册，供 composite 按 code 委托） ----------
# gold 源在 collect 的唯一入口是 akshare.composite；编排增量将 import 下面两类
# 按仪器 code 分流（'AU9999'→SgeSpotFetcher、'XAU'→LondonSpotFetcher），
# 故两类只暴露 (source, instruments) 构造契约，不 @register。

class _FullHistSpotFetcher:
    """全历史单序列现货基类：接口按 symbol 一次返回全历史 OHLC 日K，
       取 close 列产出 price 观测。子类声明 interface（akshare 接口名）与
       code_symbols（仪器 code → 接口 symbol 参数）；未知 code 构建期 raise
       （seed 拼错当场暴露，同 _WideTableFetcher 约定）。fetch 忽略 trade_date
       全量返回（全历史型，幂等 upsert）；date 列名兼容中英文、值列 close/收盘，
       NaN 值/日期行跳过。"""

    source_id = "gold"      # 仅协议完备性；未注册类不参与 collect 的 source 查找
    interface: str
    code_symbols: dict[str, str]
    date_cols = ["date", "日期"]
    value_cols = ["close", "收盘"]
    lag_days = 1

    def __init__(self, source, instruments):
        self.source = source
        self.targets: dict[str, list[str]] = {}   # symbol -> [instrument_id]（声明序）
        for i in instruments:
            code = i["code"] or ""
            if code not in self.code_symbols:
                raise ValueError(
                    f"{type(self).__name__} 未支持的仪器代码: {code}"
                    f"（约定 {'|'.join(self.code_symbols)}）")
            self.targets.setdefault(self.code_symbols[code], []).append(i["id"])

    def fetch(self, trade_date: str) -> list[Observation]:
        out = []
        for symbol, iids in self.targets.items():
            df = getattr(ak, self.interface)(symbol=symbol)
            if df is None or df.empty:   # 空表短路：接口波动须返回 [] 而非报错
                continue
            c_d = col(df, self.date_cols, "日期")
            c_v = col(df, self.value_cols, "收盘")
            for _, r in df.iterrows():
                d = normalize_date(r[c_d])
                if d is None:            # 日期缺失行跳过（NaN/NaT），不崩整个抓取
                    continue
                v = r[c_v]
                if not pd.isna(v):
                    for iid in iids:
                        out.append(Observation(iid, "price", d, float(v)))
        return out


class SgeSpotFetcher(_FullHistSpotFetcher):
    """SGE 黄金现货 Au99.99：AU9999 → close（元/克，日频）。
       生产 date 列为 datetime.date 对象，normalize_date 兜住（str 兜底分支）。"""

    interface = "spot_hist_sge"
    code_symbols = {"AU9999": "Au99.99"}


class LondonSpotFetcher(_FullHistSpotFetcher):
    """伦敦金现 XAUUSD：XAU → close（美元/盎司，日频）。
       东财 forex 系接口无贵金属品种（见模块 docstring 实测注记），走新浪外盘日K
       futures_foreign_hist。"""

    interface = "futures_foreign_hist"
    code_symbols = {"XAU": "XAU"}


class SpdrGoldFetcher(_WideTableFetcher):
    """SPDR 黄金 ETF 持仓（v1.2.1）：SPDR-GLD → 总库存（吨，日频）。
       SPDR GLD 为全球最大黄金 ETF，其吨位持仓是海外实物金需求的标杆指标。
       【不注册】——composite 按仪器 code 'SPDR-GLD' import 委托（同现货两子类
       契约）。macro_cons_gold() 无参数返回全历史宽表（2004 年起），列
       商品/日期/总库存/增持减持/总价值：只取日期+总库存，增持减持/总价值与
       商品列（恒为'黄金'）不映射；metric 硬编码 'holding'，单位吨由 seed
       instruments.unit 承载，fetcher 透传原值。"""

    interface = "macro_cons_gold"

    def _build_targets(self, instruments):
        targets = []
        for i in instruments:
            code = i["code"] or ""
            if code != "SPDR-GLD":
                raise ValueError(
                    f"spdr_gold 未支持的仪器代码: {code}（约定 SPDR-GLD）")
            targets.append((i["id"], "holding", ["总库存"]))
        return targets
