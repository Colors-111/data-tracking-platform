import pytest
from backend.fetchers.base import Observation, Fetcher, register, build_fetcher

def test_observation_fields():
    o = Observation("510300", "share", "2026-10-15", 254.85, {"k": "v"})
    assert o.instrument_id == "510300" and o.metric == "share"
    assert o.trade_date == "2026-10-15" and o.value == 254.85

def test_register_and_build():
    @register("fake.demo")
    class DemoFetcher(Fetcher):
        lag_days = 0
        def fetch(self, trade_date):
            return []

    src = {"id": "demo"}
    f = build_fetcher("fake.demo", src, [])
    assert isinstance(f, DemoFetcher)
    with pytest.raises(KeyError):
        build_fetcher("nope.missing", src, [])
