"""五幕演示剧情 → 端到端接口链路验收（离线演示模式）。

覆盖演示剧情：① 需求输入 ② 方案生成 ③ 调度大屏 ④ 运维预警 ⑤ 智能对话，
并验证前端各页面所依赖的全部数据契约（驾驶舱/地图/工作台/运维/对话/知识库）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

from .conftest import requires_demo

pytestmark = requires_demo

DEMO_TEXT = "我是矿山生产主管，年产200万吨矿石，工期3年，预算1.5亿元，帮我生成矿山开采施工方案并推荐设备。"


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


def _auth_headers(c: TestClient) -> dict:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "icops2026"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------- 剧情① 登录 + 需求输入（需求分析智能体） ----------
def test_act1_login_and_dashboard_contract(c):
    h = _auth_headers(c)
    assert c.get("/api/auth/me", headers=h).status_code == 200
    # 驾驶舱页面数据契约
    d = c.get("/api/dashboard/summary", headers=h).json()
    assert d["device_total"] >= 1 and set(d["device_state"]) >= {"working", "fault"}
    assert "warnings_open" in d and "yield_today_wan_t" in d
    dev = c.get("/api/dashboard/devices", headers=h).json()
    assert len(dev) >= 1 and {"code", "work_state", "lat", "lng"} <= set(dev[0])
    tr = c.get("/api/dashboard/trends", headers=h).json()
    assert tr["points"] and {"day", "moved_t", "faults"} <= set(tr["points"][0])


# ---------- 剧情② 方案生成（方案工作台页面） ----------
def test_act2_workspace_solution_bid_selection(c):
    h = _auth_headers(c)
    for doc in ("construction", "bid", "selection"):
        r = c.post(
            "/api/solutions/plan",
            headers=h,
            json={
                "text": DEMO_TEXT
                if doc == "construction"
                else (
                    "矿山招标项目年产300万吨剥离工期2年预算2亿元请生成投标方案"
                    if doc == "bid"
                    else "土方工程年挖填150万方工期2年预算6000万元请生成设备选型方案"
                ),
                "doc_type": doc,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok", body
        p = body["payload"]
        assert len(p["bundles"]) >= 3
        assert p["title"] and len(p["chapters"]) >= 5
        if doc == "bid":
            assert len(p["bid_response_check"]) >= 6
    # Word 导出链路（前端下载文件路径）
    first = c.post(
        "/api/solutions/plan", headers=h, json={"text": DEMO_TEXT, "doc_type": "construction"}
    ).json()
    ex = c.post(
        "/api/solutions/export",
        headers=h,
        json={"payload": first["payload"], "doc_type": "construction", "title": "导出自测"},
    ).json()
    assert ex["docx"]
    f = c.get(f"/api/solutions/files/{ex['docx']}")
    assert f.status_code == 200 and len(f.content) > 3000


# ---------- 剧情③ 调度大屏（调度/地图页面契约） ----------
def test_act3_dispatch_map_contract(c):
    h = _auth_headers(c)
    run = c.post("/api/dispatch/run", headers=h, json={"trigger": "initial", "ai": True}).json()
    assert run["assignments"] and run["stats"]["idle_rate"] < 0.3
    assert run["is_suggestion"] is True
    conf = c.post("/api/dispatch/confirm", headers=h, json={"plan_id": run["plan_id"]})
    assert conf.status_code == 200
    ab = c.get("/api/dispatch/ab", headers=h).json()
    assert ab["improvements"]["idle_rate_drop"] >= 0.15
    # 地图：设备列表 + 轨迹回放
    tr = c.get("/api/dispatch/trajectory", headers=h, params={"device_code": "T01", "limit": 30})
    assert tr.status_code == 200 and len(tr.json()["points"]) >= 1
    # 知识库（对话/延伸引用）
    kb = c.get("/api/kb/search", headers=h, params={"q": "爆破单耗", "top_k": 3})
    assert kb.status_code == 200 and kb.json()["hits"]


# ---------- 剧情④ 运维预警（运维中心页面契约） ----------
def test_act4_maintenance_contract(c):
    h = _auth_headers(c)
    warns = c.get("/api/maintenance/warnings", headers=h).json()
    assert warns and {"predicted_part", "severity", "remaining_hours"} <= set(warns[0])
    diag = c.post("/api/maintenance/diagnose", headers=h, json={"text": "HYD-01"}).json()
    assert len(diag["top3"]) >= 3 and diag["top3"][0]["code"] == "HYD-01"
    wo = c.post(
        "/api/maintenance/workorders", headers=h, json={"device_code": "T02", "code": "ENG-03"}
    ).json()
    assert wo["code"].startswith("WO-") and wo["engineer"]
    pr = c.get("/api/maintenance/predict/T04", headers=h).json()
    assert pr["risky"] is True and pr["top_code"] == "HYD-01"
    # 预警一键转工单（页面按钮）
    open_warn = next(w for w in warns if w["status"] == "open")
    wo2 = c.post(
        "/api/maintenance/workorders",
        headers=h,
        json={"device_code": open_warn["device_code"], "code": open_warn["fault_code"]},
    ).json()
    assert wo2["code"].startswith("WO-")


# ---------- 剧情⑤ 智能对话（SSE 流式多轮） ----------
def test_act5_chat_stream_and_multi_turn(c):
    h = _auth_headers(c)
    sid = c.post("/api/chat/sessions", headers=h, json={"title": "五幕剧情"}).json()["session_id"]
    # 流式生成方案
    events = ""
    with c.stream(
        "POST", f"/api/chat/sessions/{sid}/messages/stream", headers=h, json={"message": DEMO_TEXT}
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line:
                events += line + "\n"
    assert "event: route" in events and "event: delta" in events and "event: done" in events
    # 多轮追问（第二套方案 TCO）
    r2 = c.post(
        f"/api/chat/sessions/{sid}/messages",
        headers=h,
        json={"message": "把上面那套方案里第二套方案的三年TCO报给我"},
    ).json()
    assert r2["agent"] in ("solution_followup", "solution")
    assert "TCO" in r2["content"] or "元" in r2["content"]
    # 会话历史 ≥2 轮
    hist = c.get(f"/api/chat/sessions/{sid}/messages", headers=h).json()
    assert sum(1 for m in hist if m["role"] == "user") >= 2
    assert any(m.get("meta", {}).get("citations") for m in hist if m["role"] == "assistant")
