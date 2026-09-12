"""批一验收：需求槽位阻塞/补录续跑 · 会话归档 · 工单六状态流转 · 设备运营 · 维修归档。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

from .conftest import requires_demo

pytestmark = requires_demo


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


def test_slot_blocking_then_continue(c):
    """关键参数缺失 -> 阻塞生成 + 清单追问；补录后自动续跑方案生成。"""
    sid = c.post("/api/chat/sessions", json={"title": "槽位验收"}).json()["session_id"]
    r1 = c.post(
        f"/api/chat/sessions/{sid}/messages", json={"message": "帮我做个矿山施工方案，年产200万吨，工期3年"}
    ).json()
    assert r1["need_more"] is True
    assert "budget_cny" in r1["missing_slots"]
    assert r1["agent"] == "requirement"
    assert any(f["key"] == "budget_cny" for f in r1["slot_form"])

    r2 = c.post(
        f"/api/chat/sessions/{sid}/slots", json={"values": {"scene_type": "mining", "budget_cny": "1.5亿"}}
    ).json()
    assert r2["need_more"] is False
    assert r2["agent"] == "solution"
    labels = {x["label"]: x["value"] for x in r2["slot_summary"]}
    assert labels["作业场景"] == "露天矿山开采"
    assert "15,000 万元" == labels["预算"]

    # 会话状态中槽位被持久化，且待续任务已清除
    lst = c.get("/api/chat/sessions").json()
    item = next(s for s in lst["active"] if s["session_id"] == sid)
    assert item["pending"] is False
    assert any(x["key"] == "budget_cny" for x in item["slot_summary"])


def test_slot_partial_submit_keeps_asking(c):
    sid = c.post("/api/chat/sessions", json={"title": "缺参继续"}).json()["session_id"]
    c.post(f"/api/chat/sessions/{sid}/messages", json={"message": "我要采购设备，预算6000万元"})
    r = c.post(f"/api/chat/sessions/{sid}/slots", json={"values": {"annual_t": "150万方"}}).json()
    assert r["need_more"] is True
    assert "duration_years" in r["missing_slots"] and "scene_type" in r["missing_slots"]


def test_session_archive_and_restore(c):
    sid = c.post("/api/chat/sessions", json={"title": "归档验收"}).json()["session_id"]
    before = c.get("/api/chat/sessions").json()["counts"]
    c.patch(f"/api/chat/sessions/{sid}", json={"status": "archived", "tags": "矿山,验收"})
    mid = c.get("/api/chat/sessions").json()
    assert mid["counts"]["archived"] == before["archived"] + 1
    item = next(s for s in mid["archived"] if s["session_id"] == sid)
    assert item["tags"] == "矿山,验收"
    c.patch(f"/api/chat/sessions/{sid}", json={"status": "active", "title": "归档验收-已恢复"})
    after = c.get("/api/chat/sessions").json()
    assert after["counts"]["archived"] == before["archived"]
    assert any(s["session_id"] == sid and s["title"] == "归档验收-已恢复" for s in after["active"])


def test_workorder_six_status_flow(c):
    """六状态流转 + 时间线留痕 + 禁止回退。"""
    code = "WO-DEMO-T02"
    detail = c.get(f"/api/maintenance/workorders/{code}").json()
    status_flow = [s["key"] for s in detail["status_flow"]]
    assert status_flow == [
        "created",
        "dispatched",
        "repairing",
        "pending_acceptance",
        "completed",
        "archived",
    ]
    cur = detail["status"]
    for to_status in status_flow[status_flow.index(cur) + 1 :]:
        r = c.patch(
            f"/api/maintenance/workorders/{code}/status",
            json={
                "to_status": to_status,
                "note": f"验收推进到 {to_status}",
                "operator": "验收脚本",
                "labor_hours": 2.0,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == to_status
        assert body["timeline"][-1]["to"] == to_status
    final = c.get(f"/api/maintenance/workorders/{code}").json()
    assert final["status"] == "archived"
    assert final["archived_at"]
    # 不允许回退
    back = c.patch(
        f"/api/maintenance/workorders/{code}/status", json={"to_status": "repairing", "note": "回退测试"}
    )
    assert back.status_code == 400


def test_operations_panel_contract(c):
    ops = c.get("/api/maintenance/operations").json()
    s = ops["summary"]
    for key in (
        "device_total",
        "working",
        "fault",
        "avg_utilization",
        "open_warnings",
        "maintenance_due",
        "spare_alerts",
    ):
        assert key in s
    assert s["device_total"] >= 1
    dev = ops["devices"][0]
    for key in ("code", "work_state", "utilization", "work_hours", "fuel_l", "health_score"):
        assert key in dev
    assert len(ops["status_flow"]) == 6
    assert isinstance(ops["spare_alerts"], list)
    assert isinstance(ops["plan_due"], list)


def test_archive_statistics(c):
    ar = c.get("/api/maintenance/archive").json()
    assert ar["summary"]["total_orders"] >= 1
    assert ar["summary"]["archived"] >= 1
    assert ar["summary"]["mttr_hours"] is not None
    assert ar["fault_distribution"] and ar["parts_consumption"]
    assert all("timeline" in w for w in ar["archived"])
