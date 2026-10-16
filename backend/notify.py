"""采集汇总推送：企业微信/钉钉 text 消息格式（二者 msgtype 兼容）。无 WEBHOOK_URL 时静默跳过。"""
import logging
import os
import httpx

log = logging.getLogger("notify")

STATUS_CN = {"ok": "✅", "empty": "⚠️空", "stale": "⚠️陈旧", "failed": "❌失败"}

def render_summary(results: list[dict]) -> str:
    lines = ["【数据追踪平台·采集汇总】"]
    for r in results:
        line = f"{STATUS_CN.get(r['status'], r['status'])} {r['source_id']}: {r['rows']} 行"
        if r.get("error"):
            line += f" — {str(r['error'])[:120]}"
        lines.append(line)
    return "\n".join(lines)

def send_summary(results: list[dict]) -> None:
    url = os.environ.get("WEBHOOK_URL")
    if not url or not results:
        return
    try:
        content = render_summary(results)
        # 企业微信 text content 上限 2048 字节（UTF-8），超限整条被拒——
        # 恰是"所有源都挂"那天消息最长，故按 1800 字节硬截断
        content = content.encode("utf-8")[:1800].decode("utf-8", "ignore")
        resp = httpx.post(url, json={"msgtype": "text",
                                     "text": {"content": content}}, timeout=10)
        # 很多故障表现为 HTTP 200 + errcode!=0（token 失效/关键词不匹配/限流）
        body = resp.json() if resp.status_code == 200 else {}
        if resp.status_code != 200 or body.get("errcode") not in (0, None):
            log.warning("webhook 推送异常：%s %s", resp.status_code, str(body)[:200])
    except Exception:
        log.warning("webhook 推送失败", exc_info=True)  # 不影响采集结果，job_runs 已留痕
