"""一次性运行：uv run python -m backend.tests.gen_fixtures
   用真实接口录制样本，供 fetcher 离线单测使用。"""
import json
from pathlib import Path
import akshare as ak

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)
DAY = "20260930"  # 已收盘的历史交易日

def dump(name, df):
    (OUT / name).write_text(
        df.to_json(orient="records", force_ascii=False), encoding="utf-8")
    print(name, len(df), "行, 列:", list(df.columns))

dump("sse_etf_scale.json", ak.fund_etf_scale_sse(DAY))
dump("futures_inventory_cu.json", ak.futures_inventory_em(symbol="cu"))
