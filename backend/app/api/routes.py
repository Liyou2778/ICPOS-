"""REST/SSE 接口路由（指导书 4.5 协议分工：资源操作 REST、对话 SSE、实时推送 WebSocket）。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, UTC
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.agents.solution import solution_agent
from backend.app.core.config import settings
from backend.app.core.db import get_db, init_db
from backend.app.core.llm_gateway import gateway
from backend.app.core.security import create_token, get_current_user, verify_password, seed_demo_users
from backend.app.models import (
    Device,
    EquipmentModel,
    FaultCode,
    KnowledgeEntry,
    LLMCallLog,
    Project,
    SolutionDocument,
    User,
    Warning,
    WorkOrder,
)
from backend.app.services import rag
from backend.app.services.diagnosis import create_workorder, diagnose_code, diagnose_text
from backend.app.services.dispatch import dispatch_service
from backend.app.services.kb import build_all_knowledge
from backend.app.services.predictive import models_ready, predict_device
from backend.app.services.operations import (
    advance_workorder,
    archive_stats,
    device_operations,
    workorder_detail,
)
from backend.app.services.predictive_corpus import (
    artifacts_ready as corpus_artifacts_ready,
    corpus_devices,
    corpus_faults,
    predict_corpus_device,
)
from backend.app.services.vectorstore import vector_store

logger = logging.getLogger("icops.api")
router = APIRouter(prefix="/api")

utcnow = lambda: datetime.now(UTC)  # noqa: E731


# ---------------- 鉴权 ----------------
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "token": create_token(user.id, user.role, user.username),
        "user": {"username": user.username, "display_name": user.display_name, "role": user.role},
    }


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "display_name": user.display_name, "role": user.role}


# ---------------- 健康 / 运维 ----------------
@router.get("/health")
def health(db: Session = Depends(get_db)):
    models = db.query(EquipmentModel).count()
    faults = db.query(FaultCode).count()
    kb_rows = db.query(KnowledgeEntry).count()
    return {
        "app": "icops-backend",
        "version": "V1.0",
        "llm_mode": gateway.mode,  # deepseek | dashscope | demo（离线演示）
        "data": {
            "equipment_models": models,
            "fault_codes": faults,
            "kb_entries": kb_rows,
            "vector_chunks": vector_store.get().count(),
        },
        "predictive_ready": models_ready(),
        "llm_last": gateway.last_meta,
        "message": "OK",
    }


@router.get("/llm/status")
def llm_status(db: Session = Depends(get_db)):
    """大模型接入自检：当前模式、密钥配置、最近一次实际调用方、调用日志。"""
    logs = db.query(LLMCallLog).order_by(LLMCallLog.id.desc()).limit(6).all()
    return {
        "mode": gateway.mode,  # deepseek | dashscope | demo
        "has_deepseek_key": settings.has_deepseek,
        "has_dashscope_key": settings.has_dashscope,
        "last_call": gateway.last_meta,
        "recent_calls": [
            {
                "provider": x.provider,
                "model": x.model,
                "scene": x.scene,
                "ok": x.ok,
                "tokens": x.prompt_tokens + x.completion_tokens,
                "cost_cny": round(x.cost_cny, 6),
                "at": x.created_at.isoformat() if x.created_at else "",
            }
            for x in logs
        ],
    }


@router.post("/llm/probe")
async def llm_probe():
    """真实调用一次大模型，用于确认 DeepSeek 是否可用（会消耗极少 token）。"""
    return await gateway.probe()


class BootstrapOut(BaseModel):
    log: list[str] = Field(default_factory=list)


@router.post("/admin/bootstrap", response_model=BootstrapOut)
def bootstrap(db: Session = Depends(get_db)):
    """一键初始化演示数据：建库 -> 知识库入库 -> 模拟数据 -> 业务演示库 -> 训练模型。"""
    log: list[str] = []
    init_db()
    stats = build_all_knowledge(db)
    log.append(
        f"知识库入库：设备型号 {stats['equipment']}、故障码 {stats['fault_codes']}、"
        f"工艺分块 {stats['process_chunks']}、模板分块 {stats['template_chunks']}、"
        f"向量 {stats['vector_entries']} 条"
    )
    from data.simulator import gen as sim_gen

    g = sim_gen.gen(days=30)
    log.append(f"模拟数据生成：{g['rows']} 行 / {g['devices']} 台 / {g['faults']} 例故障")
    from scripts import load_demo as ld

    r = ld.run()
    log.append(f"业务演示库装载：{r['devices']} 台设备、{r['warnings']} 条预警、{r['workorders']} 张工单")
    try:
        from scripts import train_models as tm

        rep = tm.train(quiet=True)
        log.append(
            f"预测模型训练完成：准确率 {rep.get('accuracy', 0) * 100:.1f}%，"
            f"预警提前量 {rep.get('lead_hours', 0):.0f} 小时（模拟数据验证）"
        )
    except Exception as exc:  # noqa: BLE001
        log.append(f"模型训练跳过：{exc}")
    seed_demo_users(db)
    log.append("初始化完成")
    return BootstrapOut(log=log)


# ---------------- 知识库 ----------------
@router.get("/kb/search")
def kb_search(q: str, kb_type: str | None = None, top_k: int = 5, db: Session = Depends(get_db)):
    hits = rag.hybrid_search(db, q, top_k=top_k, kb_type=kb_type)
    return {"query": q, "hits": [h.as_dict() for h in hits]}


@router.get("/kb/models")
def kb_models(category: str | None = None, db: Session = Depends(get_db)):
    q = db.query(EquipmentModel)
    if category:
        q = q.filter(EquipmentModel.category == category)
    rows = q.order_by(EquipmentModel.category, EquipmentModel.code).all()
    return [
        {
            "code": m.code,
            "brand": m.brand,
            "model_name": m.model_name,
            "category_cn": m.category_cn,
            "scene": m.scene,
            "price_cny": m.price_cny,
            "rated_load_t": m.rated_load_t,
            "bucket_m3": m.bucket_m3,
            "power_kw": m.power_kw,
            "fuel_lh": m.fuel_lh,
            "spec": m.spec,
            "data_note": m.data_note,
            "source": m.source,
        }
        for m in rows
    ]


@router.get("/kb/faults")
def kb_faults(keyword: str = "", category: str = "", db: Session = Depends(get_db)):
    q = db.query(FaultCode)
    if category:
        q = q.filter(FaultCode.category == category)
    if keyword:
        q = q.filter(FaultCode.keywords.like(f"%{keyword}%") | FaultCode.name.like(f"%{keyword}%"))
    rows = q.order_by(FaultCode.code).limit(200).all()
    return [
        {
            "code": f.code,
            "name": f.name,
            "category": f.category,
            "severity": f.severity,
            "causes": f.causes,
            "fix_plan": f.fix_plan,
            "parts": f.parts,
            "est_hours": f.est_hours,
            "maintain_cost_cny": f.maintain_cost_cny,
        }
        for f in rows
    ]


@router.get("/kb/stats")
def kb_stats(db: Session = Depends(get_db)):
    return {
        "equipment_models": db.query(EquipmentModel).count(),
        "fault_codes": db.query(FaultCode).count(),
        "kb_entries": db.query(KnowledgeEntry).count(),
        "vector_chunks": vector_store.get().count(),
        "ready": db.query(EquipmentModel).count() > 0 and vector_store.get().count() > 0,
    }


@router.get("/config/frontend")
def frontend_config():
    """前端运行时配置：地图双模式（填了高德 Key/安全码才启用真实地图）。"""
    return {
        "amap_enabled": bool(settings.amap_key),
        "amap_key": settings.amap_key,
        "amap_security_code": settings.amap_security_code,
    }


# ---------------- 运营驾驶舱 ----------------
@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    total = db.query(Device).count()
    by_state = {
        s: db.query(Device).filter(Device.work_state == s).count()
        for s in ("working", "idle", "fault", "maintenance")
    }
    warnings_open = db.query(Warning).filter(Warning.status == "open").count()
    workorders = db.query(WorkOrder).count()
    proj = db.query(Project).order_by(Project.id).first()
    # 今日作业量（演示口径：按项目 30 天演示窗口均摊，单位 万吨）
    yield_today_wan_t = round(proj.work_volume / 30.0, 1) if proj and proj.work_volume else 0
    return {
        "device_total": total,
        "device_state": by_state,
        "idle_rate": round(by_state.get("idle", 0) / max(total, 1), 4),
        "warnings_open": warnings_open,
        "workorders_total": workorders,
        "project": {
            "name": proj.name if proj else "",
            "progress_pct": round((proj.progress_pct or 0) * 100, 1) if proj else 0,
        },
        "yield_today_wan_t": yield_today_wan_t,
    }


@router.get("/dashboard/devices")
def dashboard_devices(db: Session = Depends(get_db)):
    """设备清单（含型号/类别，供地图与列表）。"""
    rows = (
        db.query(Device, EquipmentModel)
        .join(EquipmentModel, Device.model_id == EquipmentModel.id)
        .order_by(Device.id)
        .all()
    )
    return [
        {
            "id": d.id,
            "code": d.code,
            "name": d.name,
            "work_state": d.work_state,
            "lat": d.lat,
            "lng": d.lng,
            "cur_load_t": d.cur_load_t,
            "status_note": d.status_note,
            "model_code": m.code,
            "category": m.category,
            "category_cn": m.category_cn,
            "model_name": m.model_name,
        }
        for d, m in rows
    ]


@router.get("/dashboard/trends")
def dashboard_trends():
    """近 N 天趋势：日运量 / 作业小时 / 故障告警（模拟数据口径）。"""
    import pandas as pd

    csv_path = settings.repo_root / "data" / "simulated" / "telemetry.csv"
    if not csv_path.exists():
        return {"points": []}
    df = pd.read_csv(csv_path)
    df["day"] = df["day"].astype(int)
    trucks = df[df["category"] == "truck"]
    daily = (
        trucks.groupby("day")
        .agg(moved_t=("load_t", "sum"), work_hours=("state", lambda s: int((s == "working").sum())))
        .reset_index()
    )
    faults = df[df["precursor"] == 1].groupby("day").size().rename("faults")
    daily = daily.merge(faults, left_on="day", right_index=True, how="left").fillna(0)
    points = [
        {
            "day": int(r.day),
            "date": f"D{int(r.day):02d}",
            "moved_t": round(float(r.moved_t), 1),
            "work_hours": int(r.work_hours),
            "fuel_rate_avg": 0.0,
            "faults": int(r.faults),
        }
        for r in daily.itertuples()
    ]
    return {"points": points}


# ---------------- 方案生成（含导出） ----------------
class PlanRequest(BaseModel):
    text: str = Field(..., description="客户需求自然语言")
    doc_type: str = Field("construction", description="construction|selection|bid")


@router.post("/solutions/plan")
def plan_solution(body: PlanRequest, db: Session = Depends(get_db)):
    start = time.perf_counter()
    art = solution_agent.handle(db, body.text, doc_type=body.doc_type)
    if art.message.startswith("需求信息不完整") or "关键参数缺失" in art.message:
        return {"status": "need_more", "questions": art.facts, "agent": art.agent}
    doc = SolutionDocument(
        doc_type=body.doc_type,
        title=art.payload.get("title", "方案"),
        payload=json.dumps(art.payload, ensure_ascii=False),
        model_version="demo-template",
        kb_version="V1.0",
        meta=json.dumps({"agent": art.agent}, ensure_ascii=False),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    payload = dict(art.payload)
    payload["document_id"] = doc.id
    return {
        "status": "ok",
        "latency_s": round(time.perf_counter() - start, 2),
        "payload": payload,
        "facts": art.facts,
        "citations": art.citations,
    }


class ExportRequest(BaseModel):
    payload: dict = Field(..., description="方案 payload（来自 /solutions/plan 的 payload）")
    doc_type: str = "construction"
    title: str = "智工云枢智能方案"


@router.post("/solutions/export")
def export_solution(body: ExportRequest):
    from backend.app.services.documents import export_document

    try:
        files = export_document(body.payload, body.doc_type, body.title)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"文档生成失败：{exc}") from exc
    return {
        "docx": Path(files["docx"]).name,
        "pdf": Path(files["pdf"]).name if files.get("pdf") else None,
        "note": "PDF 转换依赖 LibreOffice；未安装时仅返回 Word" if not files.get("pdf") else "",
    }


@router.get("/solutions/files/{fname}")
def solution_file(fname: str):
    root = Path(__file__).resolve().parents[3] / "data" / "generated"
    p = (root / fname).resolve()
    if not str(p).startswith(str(root.resolve())) or not p.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(p), filename=fname)


# ---------------- 调度 ----------------
class DispatchRunIn(BaseModel):
    trigger: str = "initial"
    fault_device_code: str | None = None
    ai: bool = True


@router.post("/dispatch/run")
def dispatch_run(body: DispatchRunIn, db: Session = Depends(get_db)):
    out = dispatch_service.run(db, trigger=body.trigger, fault_device_code=body.fault_device_code, ai=body.ai)
    return out.model_dump()


class DispatchConfirmIn(BaseModel):
    plan_id: int


@router.post("/dispatch/confirm")
def dispatch_confirm(body: DispatchConfirmIn, db: Session = Depends(get_db)):
    return dispatch_service.confirm(db, body.plan_id).model_dump()


@router.get("/dispatch/ab")
def dispatch_ab(db: Session = Depends(get_db)):
    rep = dispatch_service.ab_compare(db)
    return rep.model_dump()


@router.get("/dispatch/trajectory")
def dispatch_trajectory(device_code: str, limit: int = 120, db: Session = Depends(get_db)):
    from backend.app.services.predictive import device_telemetry

    df = device_telemetry(device_code)
    if df.empty:
        return {"device_code": device_code, "points": []}
    df = df.tail(limit)
    return {
        "device_code": device_code,
        "points": [
            {
                "ts": r.ts,
                "lat": r.lat,
                "lng": r.lng,
                "state": r.state,
                "speed_kmh": r.speed_kmh,
                "load_t": r.load_t,
            }
            for r in df.itertuples()
        ],
    }


# ---------------- 运维 ----------------
class DiagnoseIn(BaseModel):
    text: str = Field(..., description="故障描述或故障代码，如 HYD-01 / 挖掘机液压油温高漏油")
    device_code: str = ""


@router.post("/maintenance/diagnose")
def maintenance_diagnose(body: DiagnoseIn, db: Session = Depends(get_db)):
    code = body.text.strip().upper()
    if code and db.query(FaultCode).filter(FaultCode.code == code).first():
        return diagnose_code(db, code).model_dump()
    return diagnose_text(db, body.text).model_dump()


class WorkOrderIn(BaseModel):
    device_code: str
    text: str = ""
    code: str = ""


@router.post("/maintenance/workorders")
def maintenance_workorder(body: WorkOrderIn, db: Session = Depends(get_db)):
    result = diagnose_code(db, body.code) if body.code else diagnose_text(db, body.text or body.code)
    return create_workorder(db, body.device_code, result).model_dump()


@router.get("/maintenance/warnings")
def maintenance_warnings(status: str = "", db: Session = Depends(get_db)):
    q = db.query(Warning)
    if status:
        q = q.filter(Warning.status == status)
    rows = q.order_by(Warning.id.desc()).limit(100).all()
    return [
        {
            "id": w.id,
            "device_code": db.get(Device, w.device_id).code if db.get(Device, w.device_id) else "",
            "predicted_part": w.predicted_part,
            "probable_cause": w.probable_cause,
            "severity": w.severity,
            "advice": w.advice,
            "remaining_hours": w.remaining_hours,
            "model_conf": w.model_conf,
            "status": w.status,
            "fault_code": w.fault_code,
            "created_at": w.created_at.isoformat() if w.created_at else "",
        }
        for w in rows
    ]


@router.get("/maintenance/predict/{device_code}")
def maintenance_predict(device_code: str, db: Session = Depends(get_db)):
    return predict_device(db, device_code)


@router.get("/maintenance/workorders")
def maintenance_workorders(db: Session = Depends(get_db)):
    rows = db.query(WorkOrder).order_by(WorkOrder.id.desc()).limit(100).all()
    return [
        {
            "code": w.code,
            "fault_desc": w.fault_desc,
            "fault_code": w.fault_code,
            "diagnosis": w.diagnosis,
            "status": w.status,
            "engineer": w.engineer,
            "parts": w.parts,
            "est_hours": w.est_hours,
            "device_code": db.get(Device, w.device_id).code if db.get(Device, w.device_id) else "",
        }
        for w in rows
    ]


# ---------------- 语料模型（企业级训练产物在线推理） ----------------
@router.get("/maintenance/corpus/devices")
def corpus_device_list():
    """语料站点设备清单（data/simulated/corpus_meta.json）。"""
    return {"devices": corpus_devices(), "ready": corpus_artifacts_ready()}


@router.get("/maintenance/corpus/faults")
def corpus_fault_list():
    """语料故障真值（含 detect_ts/onset_ts/lead_hours），用于演示与核验。"""
    return {"faults": corpus_faults()}


@router.get("/maintenance/corpus/model-report")
def corpus_model_report():
    """语料模型评估报告（P1 时序留出 / P2 跨设备参考）。"""
    f = settings.repo_root / "data" / "models" / "corpus" / "eval_report.json"
    if not f.exists():
        raise ValueError("语料模型报告不存在：请先执行 uv run python -m scripts.train_models_corpus")
    import json as _json

    return _json.loads(f.read_text(encoding="utf-8"))


@router.get("/maintenance/predict-corpus/{device_id}")
def maintenance_predict_corpus(device_id: str, at: str | None = None):
    """语料设备部件级风险推理（机理分组多检测器 + 误报预算标定阈值）。"""
    return predict_corpus_device(device_id, at_ts=at)


# ---------------- 智能运维：设备运营 / 工单状态机 / 维修归档 ----------------
@router.get("/maintenance/operations")
def maintenance_operations(db: Session = Depends(get_db)):
    """设备运营栏：台账与实时状态、利用率/工时/能耗、保养到期、健康评分、备件预警。"""
    return device_operations(db)


class WorkOrderStatusIn(BaseModel):
    to_status: str = Field(
        ..., description="created|dispatched|repairing|pending_acceptance|completed|archived"
    )
    note: str = ""
    operator: str = ""
    labor_hours: float | None = None
    repair_notes: str | None = None
    parts_used: list | None = None


@router.patch("/maintenance/workorders/{code}/status")
def workorder_status(code: str, body: WorkOrderStatusIn, db: Session = Depends(get_db)):
    """推进工单状态（六状态机，留痕到 WorkOrderEvent）。"""
    return advance_workorder(
        db,
        code,
        body.to_status,
        note=body.note,
        operator=body.operator,
        labor_hours=body.labor_hours,
        repair_notes=body.repair_notes,
        parts_used=body.parts_used,
    )


@router.get("/maintenance/workorders/{code}")
def workorder_get(code: str, db: Session = Depends(get_db)):
    """工单详情（含状态时间线与流转记录）。"""
    return workorder_detail(db, code)


@router.get("/maintenance/archive")
def maintenance_archive(db: Session = Depends(get_db)):
    """维修归档：归档工单 + MTTR / 故障分布 / 备件消耗 / 复发统计。"""
    return archive_stats(db)
