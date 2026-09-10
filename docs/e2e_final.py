# 最终端到端验证：SPA 托管 + 全接口 + SSE 流式对话 + WebSocket 实时推送。
# 用法：python docs/e2e_final.py http://127.0.0.1:8002
import asyncio
import json
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002"
DEMO_TEXT = "年产200万吨矿石，工期3年，预算1.5亿元，帮我生成矿山施工方案并推荐设备"


def check(name, cond, extra=""):
    mark = "✅" if cond else "❌"
    print(f"{mark} {name} {extra}")
    return cond


def main() -> int:
    ok = True
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.get("/")
    ok &= check("SPA 首页(/)返回应用 HTML", r.status_code == 200 and "<div id=\"root\">" in r.text)
    r = c.get("/dashboard")
    ok &= check("客户端路由 /dashboard 回退到 index.html", r.status_code == 200)

    h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": "icops2026"}).json()["token"]}

    health = c.get("/api/health", headers=h).json()
    ok &= check("health: demo 模式 + 数据就绪", health["llm_mode"] == "demo" and health["data"]["equipment_models"] > 0)

    d = c.get("/api/dashboard/summary", headers=h).json()
    ok &= check("驾驶舱 summary", d["device_total"] >= 1)
    dev = c.get("/api/dashboard/devices", headers=h).json()
    ok &= check("设备列表 devices", len(dev) >= 1)
    tr = c.get("/api/dashboard/trends", headers=h).json()
    ok &= check("趋势 trends", len(tr["points"]) >= 25, f"({len(tr['points'])} 天)")

    plan = c.post("/api/solutions/plan", json={"text": DEMO_TEXT, "doc_type": "construction"}, headers=h).json()
    ok &= check("方案生成 3 套 + 章节", plan["status"] == "ok" and len(plan["payload"]["bundles"]) >= 3
                and len(plan["payload"]["chapters"]) >= 5)
    ex = c.post("/api/solutions/export", json={"payload": plan["payload"], "doc_type": "construction", "title": "验收"},
                headers=h).json()
    f = c.get(f"/api/solutions/files/{ex['docx']}", headers=h)
    ok &= check("Word 导出下载", ex.get("docx") and f.status_code == 200 and len(f.content) > 3000)

    ab = c.get("/api/dispatch/ab", headers=h).json()
    ok &= check("调度 A/B 空载率下降 ≥15%", ab["improvements"]["idle_rate_drop"] >= 0.15,
                f"(下降 {ab['improvements']['idle_rate_drop'] * 100:.0f}%)")
    trj = c.get("/api/dispatch/trajectory", params={"device_code": "T01", "limit": 24}, headers=h)
    ok &= check("轨迹回放", trj.status_code == 200 and len(trj.json()["points"]) > 0)

    diag = c.post("/api/maintenance/diagnose", json={"text": "HYD-01"}, headers=h).json()
    ok &= check("故障码诊断 Top≥3", len(diag["top3"]) >= 3)
    wo = c.post("/api/maintenance/workorders", json={"device_code": "T02", "code": "ENG-03"}, headers=h).json()
    ok &= check("工单生成", wo["code"].startswith("WO-"))
    pr = c.get("/api/maintenance/predict/T04", headers=h).json()
    ok &= check("预测 T04 命中风险", pr.get("risky") and pr.get("top_code") == "HYD-01")

    sid = c.post("/api/chat/sessions", json={"title": "验收"}, headers=h).json()["session_id"]
    events = ""
    with c.stream("POST", f"/api/chat/sessions/{sid}/messages/stream", json={"message": DEMO_TEXT}, headers=h) as resp:
        for line in resp.iter_lines():
            if line:
                events += line + "\n"
    ok &= check("SSE 流式对话(route/delta/done)",
                "event: route" in events and "event: delta" in events and "event: done" in events)

    msgs = c.get(f"/api/chat/sessions/{sid}/messages", headers=h).json()
    ok &= check("对话已持久化", any(m["role"] == "assistant" and m["content"] for m in msgs))

    async def ws_probe():
        import websockets
        async with websockets.connect(BASE.replace("http", "ws") + "/ws/telemetry") as ws:
            snap = json.loads(await asyncio.wait_for(ws.recv(), 10))
            return len(snap.get("devices", [])) > 0

    n = asyncio.run(ws_probe())
    ok &= check("WebSocket 实时推送设备快照", n)

    print("=" * 60)
    print("端到端最终验证：", "全部通过 ✅" if ok else "存在失败 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
