"""会话安全：HMAC 签名令牌 + 演示账号（MVP 轻量实现，RBAC 预留角色字段）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.models import User

DEMO_USERS = [
    {"username": "admin", "password": "icops2026", "display_name": "系统管理员", "role": "admin"},
    {"username": "sales", "password": "icops2026", "display_name": "销售工程师小王", "role": "engineer"},
    {"username": "dispatcher", "password": "icops2026", "display_name": "调度员老李", "role": "operator"},
    {"username": "service", "password": "icops2026", "display_name": "售后服务经理", "role": "engineer"},
    {"username": "mine", "password": "icops2026", "display_name": "矿山生产主管", "role": "customer"},
]


def hash_password(pw: str) -> str:
    return "sha256:" + hashlib.sha256(pw.encode("utf-8")).hexdigest()


def verify_password(pw: str, digest: str) -> bool:
    return digest == hash_password(pw)


def seed_demo_users(db: Session) -> int:
    """建库时写入演示账号；已存在则跳过。"""
    created = 0
    for u in DEMO_USERS:
        if db.query(User).filter(User.username == u["username"]).first() is None:
            db.add(
                User(
                    username=u["username"],
                    password_hash=hash_password(u["password"]),
                    display_name=u["display_name"],
                    role=u["role"],
                )
            )
            created += 1
    if created:
        db.commit()
    return created


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_token(user_id: int, role: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "name": username,
        "exp": int(time.time()) + settings.token_ttl_hours * 3600,
        "jti": uuid.uuid4().hex,
    }
    body = _b64(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(settings.jwt_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str) -> dict:
    try:
        body, sig = token.split(".")
        expect = hmac.new(settings.jwt_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            raise HTTPException(status_code=401, detail="令牌签名无效")
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < time.time():
            raise HTTPException(status_code=401, detail="令牌已过期")
        return payload
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="令牌无效") from exc


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    """可选鉴权：带有效令牌 -> 对应用户；无令牌 -> 演示管理员（MVP 允许无登录演示）。"""
    if authorization and authorization.lower().startswith("bearer "):
        payload = verify_token(authorization[7:].strip())
        user = db.get(User, int(payload["sub"]))
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        return user
    # 未带令牌：回退演示管理员，保证评审现场可直接演示
    user = db.query(User).filter(User.username == "admin").first()
    if user is None:
        raise HTTPException(
            status_code=401, detail="演示账号未初始化，请先执行 uv run python -m scripts.init_app"
        )
    return user
