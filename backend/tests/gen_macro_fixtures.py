"""一次性运行：uv run python -m backend.tests.gen_macro_fixtures
   用真实接口录制 v1.2 宏观 fetcher 样本，供离线单测使用（与 gen_fixtures.py 同套路）。
   实测记录（2026-10-16，akshare 1.19.x）：
   - macro_euro_lme_stock: 2718 行全历史（2014-07-03~2026-07-17，金十源尾部滞后约 3 个月），
     日期 + 镍/铝/锌/铅/锡/铜 各三列（库存/注册仓单/注销仓单，单位吨）——截尾近 400 行控制体积
   - macro_euro_lme_holding: 756 行（2015-10-22~2021-09-03，金十已停更约 5 年），
     日期 + 各品种 多头/空头/净仓位（张）——全量录制
   - CFTC：任务书预期的 macro_usa_cftc_nc_holding 实测为【外汇】非商业持仓（美元/欧元/日元…，
     无商品列）。商品非商业在 macro_usa_cftc_c_holding（dc_cftc_c_report，原油/原糖/大豆/豆油/
     豆粕/白银/铂金/钯金/天然气/黄金/棉花/玉米 12 品种，周频，日期=报告周二）。四接口均
     【无铜品种】，铜需直连 CFTC COT 官方报告——此处只录黄金相关列
   - macro_china_foreign_exchange_gold: 420 行月频（1978.12~2026.9），统计时间 'YYYY.M'，
     黄金储备单位万盎司——全量录制
   - spot_hist_sge(symbol='Au99.99')（v1.2 增量同日补录）：2382 行全历史
     （2016-12-19~2026-10-16），列 date/open/close/low/high，date 为 datetime.date
     对象（to_json 落盘 ISO 串、read_json 回读 Timestamp），close 元/克——截尾近 400 行
   - 伦敦金现：任务书预期的东财路线实测不通——forex_hist_em 的 symbol_market_map 仅含
     外汇对（无 XAU/XAG，KeyError by design）、forex_spot_em 东财限流 ConnectionError、
     裸 kline secid 119.XAU/120.XAU 返回 0 根。退路实测新浪外盘日K可用：
     futures_foreign_hist(symbol='XAU')，5186 行全历史（2006-10-16~2026-10-16），列
     date/open/high/low/close/volume/position/s，close 美元/盎司——截尾近 400 行
   """
from pathlib import Path

import akshare as ak

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)

def dump(name, df):
    (OUT / name).write_text(
        df.to_json(orient="records", force_ascii=False), encoding="utf-8")
    print(name, len(df), "行, 列:", list(df.columns))

# LME 库存/仓单：全历史 2718 行，截尾近 400 行（fetcher 全量语义不受影响——fixture 只是样本）
lme = ak.macro_euro_lme_stock()
dump("macro_lme_stock.json", lme.tail(400))

# LME 多空持仓：全量 756 行
dump("macro_lme_holding.json", ak.macro_euro_lme_holding())

# CFTC 商品非商业：取 日期 + 黄金三列（接口无铜，见模块 docstring）
cftc = ak.macro_usa_cftc_c_holding()
gold_cols = [c for c in cftc.columns if c == "日期" or c.startswith("黄金")]
dump("macro_cftc_nc.json", cftc[gold_cols])

# 央行黄金储备：全量 420 行月频
dump("macro_gold_reserve.json", ak.macro_china_foreign_exchange_gold())

# v1.2 增量（同日补录）：SGE 黄金现货 + 伦敦金现——供 composite 委托的两个子 fetcher，
# 路线结论见文件头 docstring（东财不通，伦敦金走新浪 futures_foreign_hist）
sge = ak.spot_hist_sge(symbol="Au99.99")
dump("macro_sge_spot.json", sge.tail(400))
dump("macro_london_gold.json", ak.futures_foreign_hist(symbol="XAU").tail(400))
