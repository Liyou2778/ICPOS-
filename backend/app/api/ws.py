"""WebSocket 实时推送：设备位置/告警（指导书 4.5：推送周期 ≤10 秒；断连降级轮询由前端处理）。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, UTC

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.core.db import SessionLocal
from backend.app.models import Device, Warning

router = APIRouter()


def _snapshot() -> dict:
    db = SessionLocal()
    try:
        devices = db.query(Device).all()
        warnings = db.query(Warning).filter(Warning.status == "open").count()
        return {
            "ts": datetime.now(UTC).isoformat(),
            "devices": [
                {
                    "code": d.code,
                    "name": d.name,
                    "state": d.work_state,
                    "lat": d.lat,
                    "lng": d.lng,
                    "load_t": d.cur_load_t,
                    "note": d.status_note,
                }
                for d in devices
            ],
            "open_warnings": warnings,
        }
    finally:
        db.close()


@router.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            payload = json.dumps(_snapshot(), ensure_ascii=False)
            await ws.send_text(payload)
            await asyncio.sleep(5)  # ≤10s 推送周期
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
