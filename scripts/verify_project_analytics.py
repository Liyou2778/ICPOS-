"""项目运营分析接口验收探针（不依赖 HTTP 服务，直接调用服务层 + TestClient）。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.main import app

c = TestClient(app)


def show(name: str, resp, keys: int = 6) -> None:
    assert resp.status_code == 200, f"{name} HTTP {resp.status_code}: {resp.text[:400]}"
    data = resp.json()
    print(f"\n=== {name} ===")
    if isinstance(data, dict):
        for i, (k, v) in enumerate(data.items()):
            if i >= keys:
                break
            s = json.dumps(v, ensure_ascii=False)
            print(f"  {k}: {s[:260]}")
    else:
        print(json.dumps(data, ensure_ascii=False)[:400])
    return data


show("summary", c.get("/api/projects/analytics/summary"))
pl = show("projects", c.get("/api/projects/analytics/projects"))
show("cost-structure", c.get("/api/projects/analytics/cost-structure/B1506002026080702"))
show("tender-anchor", c.get("/api/projects/analytics/tender-anchor/B1506002026080702"))
show(
    "cost-forecast",
    c.post(
        "/api/projects/analytics/cost-forecast",
        json={"section_est_total_yuan": 56941584, "duration_days": 235},
    ),
)
show(
    "task-delay-risk",
    c.post(
        "/api/projects/analytics/task-delay-risk",
        json={"process": "hauling", "plan_days": 12, "workload": 900},
    ),
)
rep = show("model-report", c.get("/api/projects/analytics/model-report"), keys=3)
print("\n上线决策:", json.dumps(rep["deploy_decision"]["ml_deployed"], ensure_ascii=False))
print(
    "错误码校验:",
    c.get("/api/projects/analytics/cost-structure/NOT-EXIST").status_code,
    c.post("/api/projects/analytics/cost-forecast", json={"section_est_total_yuan": 0}).status_code,
    c.post("/api/projects/analytics/cost-forecast", json={"section_est_total_yuan": -1}).status_code,
)
print("\n项目数:", len(pl["projects"]))
