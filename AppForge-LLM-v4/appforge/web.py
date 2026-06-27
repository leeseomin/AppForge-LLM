from __future__ import annotations

import argparse
import threading
import webbrowser
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .constants import RESOURCE_DIR
from .web_jobs import JobManager, WebConfig, WebJobError

WEB_DIR = RESOURCE_DIR / "web"
ASSET_DIR = WEB_DIR / "assets"


class CreateJobRequest(BaseModel):
    prompt: str


def _web_file(filename: str, media_type: str) -> FileResponse:
    path = WEB_DIR / filename
    if not path.is_file():
        raise WebJobError(
            "WEB_ASSET_MISSING",
            f"웹 UI 파일을 찾을 수 없습니다: {path}",
            action="프론트엔드를 빌드하거나 패키지를 다시 설치하세요.",
            status_code=500,
        )
    return FileResponse(path, media_type=media_type)


def _index_response() -> FileResponse:
    return _web_file("index.html", "text/html; charset=utf-8")


def create_app(
    config: WebConfig | None = None,
    *,
    manager: JobManager | None = None,
) -> FastAPI:
    resolved_config = config or WebConfig.from_env()
    resolved_manager = manager or JobManager(resolved_config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        resolved_manager.shutdown()

    app = FastAPI(
        title="AppForge-LLM v4",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.job_manager = resolved_manager
    app.state.web_config = resolved_config

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "manifest-src 'self'; "
            "worker-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(WebJobError)
    async def web_job_error_handler(_request: Request, exc: WebJobError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        error = {
            "code": "INVALID_REQUEST",
            "title": "입력 내용을 확인해 주세요",
            "message": "요청 형식이 올바르지 않습니다.",
            "action": "앱 설명을 입력한 뒤 다시 실행하세요.",
            "context": {"validation": exc.errors()},
        }
        return JSONResponse(status_code=422, content={"error": error})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        error = {
            "code": "INTERNAL_SERVER_ERROR",
            "title": "서버 오류가 발생했습니다",
            "message": f"{type(exc).__name__}: {exc}",
            "action": "서버 로그를 확인한 뒤 웹앱을 다시 시작하세요.",
            "context": {},
        }
        return JSONResponse(status_code=500, content={"error": error})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return resolved_manager.health()

    @app.post("/api/jobs", status_code=202)
    async def create_job(payload: CreateJobRequest) -> dict[str, Any]:
        return resolved_manager.create_job(payload.prompt)

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        return resolved_manager.get_job(job_id)

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str) -> FileResponse:
        path, filename = resolved_manager.download_path(job_id)
        return FileResponse(
            path,
            filename=filename,
            media_type="application/zip",
            headers={"Cache-Control": "no-store"},
        )

    if ASSET_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(ASSET_DIR)), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> FileResponse:
        return _web_file("favicon.svg", "image/svg+xml")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return _web_file("manifest.webmanifest", "application/manifest+json")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return _index_response()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/") or full_path.startswith("assets/"):
            raise HTTPException(status_code=404)
        return _index_response()

    return app


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
    log_level: str = "info",
) -> None:
    config = WebConfig.from_env()
    app = create_app(config)
    if open_browser:
        url = f"http://{host}:{port}"
        timer = threading.Timer(0.8, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="appforge-web",
        description="Run the AppForge-LLM v4 Vite + Vue web interface.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
    )
    args = parser.parse_args()
    serve(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
