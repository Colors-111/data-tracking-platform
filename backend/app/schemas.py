"""API 响应的心智模型（返回直接用 dict，schema 仅作文档，字段校验从简）：

- SourceGroup: {group: str, sources: list[SourceSummary]}
- SourceSummary: {id, name, group_name, unit, sort_order, inst_count, latest_date}
- SourceDetail: {id, name, unit, as_of_date: str|None,
                 cards: list[SourceCard], series: dict[instrument_id, list[(date, value)]]}
- SourceCard: {instrument_id, name, code, value,
               day_change, day_pct, range_change, range_pct}  # None 表示无法计算
"""
