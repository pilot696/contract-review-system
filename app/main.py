"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.admin import router as system_router
from app.api.pages import router as pages_router
from app.api.rules import router as rules_router
from app.api.tasks import router as tasks_router
from app.api.tools import router as tools_router
from app.config import get_settings
from app.db import engine
from app.errors import AppError
from app.logging import setup_logging
from app.services.task_service import recover_stale_tasks

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    setup_logging(settings)
    logger.info(
        "contract-review-system %s starting (env=%s, db=%s)",
        __version__,
        settings.app_env,
        settings.database_url.split("@")[-1],
    )
    try:
        recovered = recover_stale_tasks(settings)
        if recovered:
            logger.warning("recovered %s stale task(s) as blocked", recovered)
    except Exception as exc:  # noqa: BLE001 - 启动恢复失败不应阻止服务启动
        logger.warning("startup recovery skipped: %s", exc)
    yield
    engine.dispose()
    logger.info("contract-review-system stopped")


def _error_response(status_code: int, code: str, message: str, detail: object = None) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if detail is not None:
        error["detail"] = detail
    return JSONResponse(status_code=status_code, content={"ok": False, "error": error})


def create_app() -> FastAPI:
    app = FastAPI(
        title="合同审批审查系统",
        version=__version__,
        description="合同审批审查工具服务：拉取、附件、解析、规则、回写。",
        lifespan=lifespan,
    )
    app.include_router(system_router)
    app.include_router(tools_router)
    app.include_router(tasks_router)
    app.include_router(rules_router)
    app.include_router(pages_router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> Response:
        logger.warning("app error: %s %s", exc.code, exc.message)
        if request.url.path.startswith("/console"):
            from app.api.pages import error_page

            return error_page(request, exc)
        return _error_response(exc.http_status, exc.code, exc.message, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            400, "VALIDATION_ERROR", "参数校验失败", jsonable_encoder(exc.errors())[:5]
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unexpected error: %s", exc)
        return _error_response(500, "INTERNAL_ERROR", "内部错误")

    return app


app = create_app()
