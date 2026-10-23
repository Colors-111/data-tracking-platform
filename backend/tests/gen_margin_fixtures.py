"""录制融资融券三上游接口真实样本（v1.4 设计 §5.4）。
   运行：uv run --no-sync python backend/tests/gen_margin_fixtures.py
   全量录制（对齐 macro fixture 惯例：LME 2718 行同样全量入库）。"""
from pathlib import Path

import akshare as ak

FIX = Path(__file__).parent / "fixtures"


def dump(df, name):
    df.to_json(FIX / name, orient="records", force_ascii=False, indent=1)
    print(name, len(df), "rows")


dump(ak.macro_china_market_margin_sh(), "margin_lg_sh.json")
dump(ak.macro_china_market_margin_sz(), "margin_lg_sz.json")
dump(ak.stock_margin_account_info(), "margin_account.json")
