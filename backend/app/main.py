"""ICOPS MVP 后端入口（FastAPI 单服务）。

启动（仓库根目录）：
    uv run uvicorn backend.app.main:app --reload   （或 .venv\\Scripts\\uvicorn）
接口文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.chat import router as chat_router
from backend.app.api.routes import router as api_router
from backend.app.api.ws import router as ws_router
from backend.app.core.config import settings
from backend.app.core.db import SessionLocal, init_db
from backend.app.core.security import seed_demo_users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("icops.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ICOPS MVP backend starting (env=%s, llm=%s)", settings.app_env, "见 /api/health")
    init_db()
    db = SessionLocal()
    try:
        seed_demo_users(db)
    finally:
        db.close()
    logger.info("DB initialized; demo accounts: admin / icops2026")
    yield
    from backend.app.core.llm_gateway import gateway

    await gateway.aclose()


app = FastAPI(
    title="智工云枢（ICOPS）MVP 后端",
    version="V1.0",
    description="面向工程机械全产业链的 AI 原生智能运营平台（矿山施工全流程演示闭环后端）",
    lifespan=lifespan,
)


# ---------- 统一异常规范：错误码 + message + 结构化 detail ----------
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402


@app.exception_handler(StarletteHTTPException)
async def http_exc_handler(request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "detail": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    msg = f"参数校验失败：{loc} {first.get('msg', '')}"
    return JSONResponse(status_code=422, content={"code": 422, "message": msg, "detail": exc.errors()})


@app.exception_handler(ValueError)
async def value_exc_handler(request, exc: ValueError):
    """业务规则校验失败（如演示数据未初始化）→ 400 + 可读 message。"""
    logger.warning("业务参数错误 %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=400, content={"code": 400, "message": str(exc), "detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_exc_handler(request, exc: Exception):
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "服务器内部错误（详情已记录日志）",
            "detail": f"{type(exc).__name__}: {exc}",
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 演示环境放开；生产按域名收敛
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(chat_router)
app.include_router(ws_router)


@app.get("/")
def root():
    from fastapi.responses import FileResponse

    index = settings.repo_root / "frontend" / "dist" / "index.html"
    if index.exists():
        # 单源部署：根路径直接打开 Web 应用
        return FileResponse(str(index))
    return {
        "app": "ICOPS MVP",
        "docs": "/docs",
        "health": "/api/health",
        "demo_bootstrap": "POST /api/admin/bootstrap",
        "notice": "前端尚未构建：cd frontend && npm run build；之后刷新本页进入应用",
    }


# ---------- 单源部署：若前端已构建（frontend/dist），由后端统一托管 Web 应用 ----------
DIST_DIR = settings.repo_root / "frontend" / "dist"
if DIST_DIR.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        from fastapi.responses import FileResponse, JSONResponse

        if full_path.startswith(("api/", "ws")):
            return JSONResponse({"detail": "接口不存在（API 未注册）"}, status_code=404)
        target = DIST_DIR / full_path
        if full_path and target.is_file():
            return FileResponse(str(target))
        index = DIST_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"detail": "前端未构建，请先执行 pnpm/npm run build"}, status_code=503)
