"""一次性运行：uv run python -m backend.tests.gen_commodity_fixtures
   录制商品复合 fetcher（akshare.composite）三支接口的真实样本，供离线单测使用。
   实测要点（2026-10-15）：
   - futures_zh_daily_sina('AU0')：全历史 4570+ 行，date 为 str，截尾保留近 500 行控制体积
   - futures_spot_price：生意社(100ppi)已无氧化铝（AO）行——只回 CU/AL，AO 长期缺行
   - futures_shfe_warehouse_receipt：dict[品种] = DataFrame，列 [VARNAME, REGNAME,
     WHABBRNAME, WRTWGHTS, WRTCHANGE, ROWSTATUS]；ROWSTATUS 为字符串
     （'0'明细/'1'区域合计/'2'总计）；WRTWGHTS 铜/铝/氧化铝=吨、黄金=千克"""
import json
from pathlib import Path

import akshare as ak

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)
DAY = "20261015"  # 已收盘的历史交易日

# 1) SHFE 主力连续日K（全历史型）
daily = ak.futures_zh_daily_sina(symbol="AU0")
(OUT / "commodity_shfe_daily_au0.json").write_text(
    daily.tail(500).to_json(orient="records", force_ascii=False), encoding="utf-8")
print("commodity_shfe_daily_au0.json", len(daily.tail(500)),
      f"行(全史 {len(daily)} 行), 列: {list(daily.columns)}, dtypes: {dict(daily.dtypes.astype(str))}")
print(daily.tail(2).to_string())

# 2) 生意社现货价（单日型）
spot = ak.futures_spot_price(date=DAY, vars_list=["CU", "AL", "AO"])
(OUT / "commodity_spot_price.json").write_text(
    spot.to_json(orient="records", force_ascii=False), encoding="utf-8")
print("\ncommodity_spot_price.json", len(spot), "行, 列:", list(spot.columns))
print("  注意：100ppi 已下架氧化铝——vars_list 带 AO 也无行（2024 起长期如此），fixture 如实缺 AO")
print(spot[["date", "symbol", "spot_price"]].to_string())

# 3) 上期所仓单（单日型）——整个 dict 序列化（值为 DataFrame，逐键转 records）
receipt = ak.futures_shfe_warehouse_receipt(date=DAY)
ser = {}
for k, v in receipt.items():
    ser[k] = json.loads(v.to_json(orient="records", force_ascii=False)) \
        if hasattr(v, "to_json") else v
(OUT / "commodity_shfe_warrant.json").write_text(
    json.dumps(ser, ensure_ascii=False), encoding="utf-8")
print("\ncommodity_shfe_warrant.json keys:", list(receipt.keys()))
for k in ["铜", "铝", "氧化铝仓库", "氧化铝厂库", "黄金", "白银"]:
    v = receipt[k]
    rs = v["ROWSTATUS"].astype(str)   # 实测 ROWSTATUS 为字符串而非整数
    detail = v[rs == "0"]["WRTWGHTS"].sum()
    total = v[rs == "2"]["WRTWGHTS"].tolist()
    print(f"  {k}: rows={len(v)} 明细求和={detail} 总计行={total} 列={list(v.columns)}")
