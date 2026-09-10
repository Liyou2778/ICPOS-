"""API 集成冒烟：登录/驾驶舱/方案/对话/诊断（离线演示模式下执行）。"""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

from .conftest import requires_demo

pytestmark = requires_demo

DEMO_TEXT = (
    "我是矿山生产主管，新接到矿区开采任务：年产 200 万吨，工期 3 年，"
    "预算 1.5 亿元。帮我生成矿山开采施工方案并推荐设备。"
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_login_and_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["equipment_models"] >= 10
    assert body["data"]["fault_codes"] >= 50

    login = client.post("/api/auth/login", json={"username": "admin", "password": "icops2026"})
    assert login.status_code == 200
    assert login.json()["token"]


def test_dashboard_summary(client):
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["device_total"] >= 1
    assert d["warnings_open"] >= 1


def test_solution_plan_three_bundles(client):
    r = client.post("/api/solutions/plan", json={"text": DEMO_TEXT, "doc_type": "construction"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    payload = body["payload"]
    assert len(payload["bundles"]) >= 3
    assert payload["title"]
    assert len(payload["chapters"]) >= 5  # 施工组织 ≥ 多章节
    assert body["citations"]  # 引用来源


def test_bid_plan_response_check(client):
    text = "某矿山招标项目：年产 300 万吨剥离，工期 2 年，预算 2 亿元。请生成投标方案。"
    r = client.post("/api/solutions/plan", json={"text": text, "doc_type": "bid"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    payload = body["payload"]
    assert len(payload["chapters"]) >= 6  # 投标方案 ≥6 章节
    assert payload.get("bid_response_check")  # 响应度检查 100% 条款覆盖
    assert all(x["status"] == "已响应" for x in payload["bid_response_check"])


def test_chat_flow_and_history(client):
    s = client.post("/api/chat/sessions", json={"title": "测试会话"})
    sid = s.json()["session_id"]
    r = client.post(f"/api/chat/sessions/{sid}/messages", json={"message": DEMO_TEXT})
    assert r.status_code == 200
    msg = r.json()
    assert msg["agent"] == "solution"
    assert msg["content"] and "方案" in msg["content"]
    assert msg["citations"]
    hist = client.get(f"/api/chat/sessions/{sid}/messages")
    roles = [m["role"] for m in hist.json()]
    assert roles.count("user") >= 1 and roles.count("assistant") >= 1


def test_chat_stream_sse(client):
    s = client.post("/api/chat/sessions", json={"title": "流式"})
    sid = s.json()["session_id"]
    lines: list[str] = []
    with client.stream(
        "POST",
        f"/api/chat/sessions/{sid}/messages/stream",
        json={"message": "矿卡 T02 水温高报警，帮我诊断并生成维修工单"},
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line:
                lines.append(line)
    events = "\n".join(lines)
    assert "event: route" in events
    assert "event: done" in events
    assert "event: delta" in events or "delta" in events


def test_dispatch_ab_and_maintenance(client):
    r = client.get("/api/dispatch/ab")
    assert r.status_code == 200
    ab = r.json()
    assert ab["improvements"]["idle_rate_drop"] >= 0.15

    diag = client.post("/api/maintenance/diagnose", json={"text": "HYD-01"})
    assert diag.status_code == 200
    assert len(diag.json()["top3"]) >= 3

    wo = client.post("/api/maintenance/workorders", json={"device_code": "T02", "code": "HYD-01"})
    assert wo.status_code == 200
    assert wo.json()["code"].startswith("WO-")


def test_kb_search_api(client):
    r = client.get("/api/kb/search", params={"q": "排土场安全车挡高度要求", "top_k": 3})
    assert r.status_code == 200
    assert len(r.json()["hits"]) >= 1
