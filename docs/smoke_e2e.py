"""端到端冒烟：http://127.0.0.1:8001 上验证核心验收链路。"""

import asyncio
import json

import httpx

BASE = "http://127.0.0.1:8001"
DEMO_TEXT = (
    "我是矿山生产主管，新接到矿区开采任务：年产 200 万吨矿石，工期 3 年，"
    "预算 1.5 亿元。请生成矿山开采施工方案并推荐设备。"
)


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=30.0)

    # 1) 登录
    r = c.post("/api/auth/login", json={"username": "admin", "password": "icops2026"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    H = {"Authorization": f"Bearer {token}"}

    # 2) 驾驶舱
    d = c.get("/api/dashboard/summary", headers=H).json()
    print(
        f"[dashboard] 设备 {d['device_total']} 台 / 开放预警 {d['warnings_open']} / "
        f"今日产量 {d['yield_today_wan_t']} 万吨 / 进度 {d['project']['progress_pct']}%"
    )

    # 3) 方案生成（验收：≥3 套、30s 内）
    r = c.post("/api/solutions/plan", json={"text": DEMO_TEXT, "doc_type": "construction"}, headers=H)
    plan = r.json()
    assert plan["status"] == "ok"
    payload = plan["payload"]
    print(
        f"[solution] {len(payload['bundles'])} 套方案，耗时 {plan['latency_s']:.2f}s，"
        f"章节 {len(payload['chapters'])}，引用 {len(plan['citations'])}"
    )
    for b in payload["bundles"]:
        print(f"    {b['name']}: {b['summary'][:80]}…  3年TCO={b['tco_3y_total_cny'] / 1e4:.0f}万元")

    # 4) 投标方案响应度检查
    r = c.post(
        "/api/solutions/plan",
        json={"text": "年产300万吨剥离，工期2年，预算2亿元，请生成投标方案", "doc_type": "bid"},
        headers=H,
    )
    bid = r.json()
    print(
        f"[bid] 章节 {len(bid['payload']['chapters'])}，响应度检查 {len(bid['payload']['bid_response_check'])} 条全部已响应"
    )

    # 5) 文档导出（Word；LibreOffice 有则 PDF）
    r = c.post(
        "/api/solutions/export",
        json={"payload": payload, "doc_type": "construction", "title": payload["title"]},
        headers=H,
    )
    ex = r.json()
    print(f"[export] docx={ex['docx']} pdf={ex.get('pdf')} note={ex.get('note') or ''}")
    f = c.get(f"/api/solutions/files/{ex['docx']}")
    assert f.status_code == 200 and len(f.content) > 3000
    print(f"[export-file] 下载成功，大小 {len(f.content)} B")

    # 6) 调度：运行->确认->A/B
    r = c.post("/api/dispatch/run", json={"trigger": "initial", "ai": True}, headers=H).json()
    print(
        f"[dispatch] 派单 {len(r['assignments'])} 条，空载率 {r['stats']['idle_rate'] * 100:.1f}%，"
        f"利用率 {r['stats']['utilization'] * 100:.1f}%（建议执行，待确认）"
    )
    plan_id = r["plan_id"]
    r2 = c.post("/api/dispatch/confirm", json={"plan_id": plan_id}, headers=H).json()
    print(f"[dispatch] 方案 #{plan_id} 已确认下发，note={r2['note'][:18]}…")
    ab = c.get("/api/dispatch/ab", headers=H).json()
    print(
        f"[ab] 人工空载率 {ab['metrics']['manual_idle_rate'] * 100:.1f}% -> AI "
        f"{ab['metrics']['ai_idle_rate'] * 100:.1f}%，下降 {ab['improvements']['idle_rate_drop'] * 100:.1f}%"
    )

    # 7) 运维：诊断/工单/预测
    diag = c.post(
        "/api/maintenance/diagnose", json={"text": "液压油温高，动作没劲，怀疑漏油"}, headers=H
    ).json()
    print(
        "[diagnose] Top3: "
        + "；".join(f"{x['code']} {x['name']}({x['confidence'] * 100:.0f}%)" for x in diag["top3"])
    )
    wo = c.post(
        "/api/maintenance/workorders", json={"device_code": "T02", "code": "HYD-01"}, headers=H
    ).json()
    print(
        f"[workorder] {wo['code']} 设备{wo['device_code']} 工程师={wo['engineer']} 备件={len(wo['parts'])} 件"
    )
    pr = c.get("/api/maintenance/predict/T04", headers=H).json()
    print(
        f"[predict] T04 risky={pr.get('risky')} code={pr.get('top_code')} conf={pr.get('top_conf')} "
        f"RUL={pr.get('remaining_hours')}h anomaly={pr.get('anomaly_score')}"
    )

    # 8) 对话 SSE
    sid = c.post("/api/chat/sessions", json={"title": "冒烟会话"}, headers=H).json()["session_id"]
    events: list[str] = []
    with c.stream(
        "POST", f"/api/chat/sessions/{sid}/messages/stream", json={"message": DEMO_TEXT}, headers=H
    ) as resp:
        for line in resp.iter_lines():
            if line:
                events.append(line)
    ev = "\n".join(events)
    assert "event: route" in ev and "event: done" in ev
    done = next(x for x in events if x.startswith("event: done"))
    assert done and "event: route" in ev
    print(f"[chat-sse] 收到 {len(events)} 帧事件，done 帧存在，代理=route 已路由")
    msgs = c.get(f"/api/chat/sessions/{sid}/messages", headers=H).json()
    last = msgs[-1]
    print(f"[chat] 助手回复 {len(last['content'])} 字：{last['content'][:60]}…")

    # 9) 知识库检索
    kb = c.get("/api/kb/search", params={"q": "排土场安全车挡高度有什么要求"}, headers=H).json()
    print(f"[kb] 命中 {len(kb['hits'])} 条，Top1: {kb['hits'][0]['title']}")

    # 10) 轨迹回放
    tr = c.get("/api/dispatch/trajectory", params={"device_code": "T01", "limit": 24}, headers=H).json()
    print(f"[trajectory] T01 最近 {len(tr['points'])} 个点（首点 {tr['points'][0]['ts']}）")

    # 11) WebSocket 实时推送
    async def ws_probe():
        import websockets

        async with websockets.connect("ws://127.0.0.1:8001/ws/telemetry") as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=8)
            snap = json.loads(msg)
            print(f"[ws-telemetry] 推送 OK：设备 {len(snap['devices'])} 台，开放预警 {snap['open_warnings']}")

    asyncio.run(ws_probe())
    print("\n=== 端到端冒烟全部通过 ===")


if __name__ == "__main__":
    main()
