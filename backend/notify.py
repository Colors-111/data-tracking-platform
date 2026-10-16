"""采集汇总推送：企业微信/钉钉 text 消息格式（二者 msgtype 兼容）。无 WEBHOOK_URL 时静默跳过。"""
import os
import httpx

STATUS_CN = {"ok": "✅", "empty": "⚠️空", "stale": "⚠️陈旧", "failed": "❌失败"}

def render_summary(results: list[dict]) -> str:
    lines = ["【数据追踪平台·采集汇总】"]
    for r in results:
        line = f"{STATUS_CN.get(r['status'], r['status'])} {r['source_id']}: {r['rows']} 行"
        if r.get("error"):
            line += f" — {r['error']}"
        lines.append(line)
    return "\n".join(lines)

def send_summary(results: list[dict]) -> None:
    url = os.environ.get("WEBHOOK_URL")
    if not url or not results:
        return
    try:
        httpx.post(url, json={"msgtype": "text",
                              "text": {"content": render_summary(results)}},
                   timeout=10)
    except Exception:
        pass  # 通知失败不影响采集结果，job_runs 已留痕
