"""一次性运行：uv run python -m backend.tests.gen_szse_fixture
   录制深交所 ETF 份额单日快照，供 fetcher 离线单测使用（与 gen_fixtures.py 并列）。"""
from pathlib import Path
import akshare as ak

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)
DAY = "20261014"  # 已收盘的历史交易日

df = ak.fund_scale_daily_szse(start_date=DAY, end_date=DAY, symbol="ETF")
(OUT / "szse_etf_scale.json").write_text(
    df.to_json(orient="records", force_ascii=False), encoding="utf-8")
print(len(df), "行, 列:", list(df.columns))
print(df[df["基金代码"].isin(["159922", "159915", "159820", "159949", "159952"])].to_string())
