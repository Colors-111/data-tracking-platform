"""fetcher 公共工具：列定位 / 代码归一 / 名称清洗 / 日期归一。
   三次法则产物——原 _col 在 cu 与两个 ETF fetcher 各有一份本地复制，
   SHARE_CANDIDATES 漂移丢过 "份额合计(亿份)" 变体，故收敛到此。"""
import datetime as dt

import numpy as np
import pandas as pd

# 两所 ETF 份额接口实测列名（Task 4 / SZSE fixture），新增变体在此统一追加
CODE_CANDIDATES = ["基金代码", "证券代码", "代码"]
SHARE_CANDIDATES = ["基金份额", "基金份额(亿份)", "份额合计(亿份)", "份额"]

def col(df, candidates, what):
    """按候选名定位列，找不到即抛 ValueError（fail-loud，禁静默产出错误数据）。"""
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(f"未找到{what}列，实际列: {list(df.columns)}")

def norm_code(v) -> str:
    """基金代码归一为 6 位字符串（to_json 往返可能丢前导零）。"""
    return str(v).zfill(6)

def clean_name(v) -> str:
    """名称列缺失→None；NaN/pd.NA 由 pd.isna 统一命中，归一空串（防 'nan' 入库）。"""
    return "" if pd.isna(v) else str(v)

def normalize_date(v, fallback: str | None = None) -> str | None:
    """日期归一为 'YYYY-MM-DD'，覆盖五形态：
       1. str/Timestamp/datetime64：取前 10 位，'/' 归一 '-'（cu 口径）；
          8 位纯数字字符串（生意社现货 'YYYYMMDD'）按整数形态解析
       2. epoch-ms 整数：UTC 锚定还原——pandas to_json 的 epoch-ms 以 UTC 午夜
          为基准，只有 UTC 转换才能无损还原日期，按北京午夜解释会差一天
       3. YYYYMMDD 整数（1e7–1e8）
       4. NaN/NaT/None → 返回 fallback：
          cu 不传（None → 调用方跳过该行），etf_scale 传请求日（单日区间兜底）。
       无法识别的其余形态按 cu 口径 str(v)[:10] 原样透出（fail-visible）。"""
    if v is None or pd.isna(v):
        return fallback
    if isinstance(v, str) and v.isdigit() and len(v) == 8:
        v = int(v)                              # 'YYYYMMDD' 字符串 → 走整数分支
    if isinstance(v, (int, float, np.integer, np.floating)):
        if 1e7 <= float(v) < 1e8:  # YYYYMMDD 整数（先判，区间与真实 epoch-ms 不重叠）
            return dt.datetime.strptime(str(int(v)), "%Y%m%d").strftime("%Y-%m-%d")
        return dt.datetime.fromtimestamp(int(v) / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")
    return str(v)[:10].replace("/", "-")   # '2026-10-14' / '2026/10/14' / Timestamp
