import pytest
from backend import notify

def test_renders_summary_with_status():
    results = [
        {"source_id": "a", "rows": 5, "status": "ok", "error": None},
        {"source_id": "b", "rows": 0, "status": "failed", "error": "RuntimeError('x')"},
    ]
    text = notify.render_summary(results)
    # STATUS_CN 将状态渲染为 emoji/中文标签，不保留英文原词
    assert "a" in text and "✅" in text and "失败" in text and "x" in text
    assert "b" in text and "0 行" in text

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
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify.send_summary([{"source_id": "a", "rows": 1, "status": "ok", "error": None}])
    assert called["url"] == "https://example.com/hook"
    assert called["payload"]["msgtype"] == "text"
