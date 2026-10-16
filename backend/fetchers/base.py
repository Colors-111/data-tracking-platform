"""Fetcher 协议与注册表。新增数据源 = 实现 Fetcher + @register + sources 配置行。"""
from dataclasses import dataclass
from typing import Protocol

@dataclass
class Observation:
    instrument_id: str
    metric: str
    trade_date: str          # 'YYYY-MM-DD'
    value: float | None
    raw: dict | None = None

class Fetcher(Protocol):
    source_id: str
    lag_days: int            # 数据发布滞后天数（T+1 源=1）
    def fetch(self, trade_date: str) -> list[Observation]: ...
    # 声明构造契约（source, instruments），与 build_fetcher 的调用一致。
    # Py3.11+ 起，继承 Protocol 且未自定义 __init__ 的子类沿 MRO 只能搜到
    # object.__init__，带参实例化会 TypeError；此存根使其可用。
    def __init__(self, source: dict, instruments: list) -> None: ...

_REGISTRY: dict[str, type] = {}

def register(name: str):
    def deco(cls):
        _REGISTRY[name] = cls
        if not getattr(cls, "source_id", None):
            cls.source_id = name.split(".")[-1]
        return cls
    return deco

def build_fetcher(name: str, source, instruments: list) -> Fetcher:
    if name not in _REGISTRY:
        raise KeyError(f"未注册的 fetcher: {name}")
    return _REGISTRY[name](source, instruments)
