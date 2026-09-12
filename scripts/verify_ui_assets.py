"""批二/批一前端资源与关键接口自检（针对运行中的服务）。

用法：python scripts/verify_ui_assets.py http://127.0.0.1:8006
"""

from __future__ import annotations

import re
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8006"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)
    ok = True
    root = c.get("/")
    print(f"SPA 首页: {root.status_code}")
    ok &= root.status_code == 200 and 'id="root"' in root.text
    assets = re.findall(r"/assets/[A-Za-z0-9._-]+\.(?:js|css)", root.text)
    for a in assets[:3]:
        r = c.get(a)
        print(f"  资源 {a}: {r.status_code} ({len(r.content)} bytes)")
        ok &= r.status_code == 200

    ops = c.get("/api/maintenance/operations").json()
    print(
        f"设备运营: {ops['summary']['device_total']} 台 / 备件预警 {ops['summary']['spare_alerts']} / "
        f"保养到期 {ops['summary']['maintenance_due']}"
    )
    ok &= ops["summary"]["device_total"] >= 1 and len(ops["status_flow"]) == 6

    ar = c.get("/api/maintenance/archive").json()
    print(
        f"维修归档: 工单 {ar['summary']['total_orders']} / 已归档 {ar['summary']['archived']} / "
        f"MTTR {ar['summary']['mttr_hours']}h"
    )
    ok &= ar["summary"]["mttr_hours"] is not None

    sess = c.get("/api/chat/sessions").json()
    print(f"会话分组: 进行中 {sess['counts']['active']} / 已归档 {sess['counts']['archived']}")

    chat = c.post("/api/chat/sessions", json={"title": "资源自检"}).json()
    r1 = c.post(
        f"/api/chat/sessions/{chat['session_id']}/messages", json={"message": "帮我做矿山方案，年产200万吨"}
    ).json()
    print(f"缺参追问: need_more={r1.get('need_more')} missing={r1.get('missing_slots')}")
    ok &= bool(r1.get("need_more"))

    llm = c.get("/api/llm/status").json()
    print(f"大模型: mode={llm['mode']}")
    print("=" * 56)
    print("自检结果：", "全部通过 ✅" if ok else "存在失败 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
