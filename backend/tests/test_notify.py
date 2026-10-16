import pytest
from backend import notify

class _Resp:
    """httpx.Response 桩：status_code + json()。"""
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = {"errcode": 0, "errmsg": "ok"} if body is None else body
    def json(self):
        return self._body

def test_renders_summary_with_status():
    results = [
        {"source_id": "a", "rows": 5, "status": "ok", "error": None},
        {"source_id": "b", "rows": 0, "status": "failed", "error": "RuntimeError('x')"},
        {"source_id": "c", "rows": 0, "status": "empty", "error": None},
        {"source_id": "d", "rows": 3, "status": "stale", "error": None},
    ]
    text = notify.render_summary(results)
    # STATUS_CN 将状态渲染为 emoji/中文标签，不保留英文原词
    assert "a" in text and "✅" in text and "失败" in text and "x" in text
    assert "b" in text and "0 行" in text
    assert "空" in text and "陈旧" in text   # empty/stale 标签防回归

def test_send_skips_without_url(monkeypatch):
    called = []
    monkeypatch.setattr(notify.httpx, "post",
                        lambda *a, **k: called.append((a, k)))
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    notify.send_summary([{"source_id": "a", "rows": 1, "status": "ok", "error": None}])
    assert called == []

def test_send_posts_wechat_format(monkeypatch):
    called = {}
    def fake_post(url, json=None, timeout=None):
        called.update(url=url, payload=json)
        return _Resp()
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify.send_summary([{"source_id": "a", "rows": 1, "status": "ok", "error": None}])
    assert called["url"] == "https://example.com/hook"
    assert called["payload"]["msgtype"] == "text"
    assert called["payload"]["text"]["content"]   # content 键必须存在且非空

def test_send_swallows_post_exception(monkeypatch):
    # webhook 抛异常不得外泄：否则超时会炸 main() → 假 OnFailure 告警
    def boom_post(url, json=None, timeout=None):
        raise RuntimeError("连接超时")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(notify.httpx, "post", boom_post)
    notify.send_summary([{"source_id": "a", "rows": 1, "status": "ok", "error": None}])  # 不抛即通过

def test_send_warns_on_nonzero_errcode(monkeypatch, caplog):
    # HTTP 200 但 errcode!=0（token 失效/关键词不匹配/限流）必须留日志，不许静默丢
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(notify.httpx, "post",
                        lambda *a, **k: _Resp(body={"errcode": 93000, "errmsg": "invalid webhook url"}))
    with caplog.at_level("WARNING", logger="notify"):
        notify.send_summary([{"source_id": "a", "rows": 1, "status": "ok", "error": None}])
    assert "93000" in caplog.text
