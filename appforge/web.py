from __future__ import annotations

import argparse
import hashlib
import json
import logging
import mimetypes
import os
import queue
import secrets
import signal
import threading
import webbrowser
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import __version__, llm_bridge
from .constants import RESOURCE_DIR
from .llm_bridge_process import LLMBridgeProcessManager
from .web_jobs import LLM_BRIDGE_DRIVER_ALIASES, JobManager, WebConfig, WebJobError

WEB_DIR = RESOURCE_DIR / "web"
ASSET_DIR = WEB_DIR / "assets"
logger = logging.getLogger(__name__)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SSE_KEEPALIVE_SECONDS = 15.0
SESSION_COOKIE_NAME = "appforge_session"


def _host_without_port(value: str) -> str:
    host = value.split(",", 1)[0].strip()
    if host.startswith("["):
        return host.split("]", 1)[0].strip("[]")
    return host.split(":", 1)[0]


def _is_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    return _host_without_port(value).casefold() in LOCAL_HOSTS


def _origin_matches_request(request: Request, value: str | None) -> bool:
    if not value:
        return False
    try:
        candidate = urlparse(value)
        expected = request.url
        candidate_port = candidate.port or (443 if candidate.scheme == "https" else 80)
        expected_port = expected.port or (443 if expected.scheme == "https" else 80)
        return (
            candidate.scheme.casefold() == expected.scheme.casefold()
            and (candidate.hostname or "").casefold() == (expected.hostname or "").casefold()
            and candidate_port == expected_port
        )
    except Exception:
        return False


def _public_bridge_context(exc: llm_bridge.BridgeError) -> dict[str, Any]:
    context: dict[str, Any] = {}
    code = exc.code
    if code:
        context["bridge_code"] = code
    if exc.status_code or not isinstance(exc.payload, dict):
        return context
    for key in ("reason", "action"):
        value = exc.payload.get(key)
        if isinstance(value, str):
            context[key] = value[:500]
    return context


def _public_bridge_message(exc: llm_bridge.BridgeError) -> str:
    if exc.status_code:
        return "LLM 브릿지 요청을 안전하게 처리하지 못했습니다. 연결 설정을 확인하세요."
    # Connection/process-manager errors are authored locally and never include
    # request headers or provider credentials.
    return str(exc)[:500]


class CreateJobRequest(BaseModel):
    prompt: str
    mode: str | None = None
    pipeline: str | None = None
    provider: str | None = None
    model: str | None = None
    generation: dict[str, Any] | None = None
    pricing: dict[str, Any] | None = None


class ReviseJobRequest(BaseModel):
    request: str
    mode: str | None = None
    pipeline: str | None = None
    provider: str | None = None
    model: str | None = None
    generation: dict[str, Any] | None = None
    pricing: dict[str, Any] | None = None


class RetryJobRequest(BaseModel):
    stage: str | None = None


class UpdateJobRequest(BaseModel):
    starred: bool | None = None
    archived: bool | None = None


class UpsertProviderRequest(BaseModel):
    apiKey: str | None = None
    clearApiKey: bool = False
    baseURL: str | None = None
    defaultModel: str | None = None


class TestProviderRequest(BaseModel):
    apiKey: str | None = None
    baseURL: str | None = None
    model: str | None = None


class ActiveProviderRequest(BaseModel):
    provider: str | None = None
    model: str | None = None


class QuickConnectRequest(BaseModel):
    provider: str
    apiKey: str
    baseURL: str | None = None
    model: str | None = None


class OAuthStartRequest(BaseModel):
    provider: str
    method: str = "browser"


class BootstrapSessionRequest(BaseModel):
    code: str


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
    llm_bridge_manager: LLMBridgeProcessManager | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    session_token: str | None = None,
    bootstrap_token: str | None = None,
) -> FastAPI:
    resolved_config = config or WebConfig.from_env()
    resolved_session_token = session_token or secrets.token_urlsafe(32)
    resolved_manager = manager or JobManager(resolved_config)
    resolved_bridge_manager = llm_bridge_manager or LLMBridgeProcessManager(
        runtime_dir=resolved_manager.data_dir,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        resolved_bridge_manager.shutdown()
        resolved_manager.shutdown()

    app = FastAPI(
        title="AppForge-LLM v7",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.job_manager = resolved_manager
    app.state.web_config = resolved_config
    app.state.llm_bridge_manager = resolved_bridge_manager
    app.state.shutdown_callback = shutdown_callback or _request_process_shutdown
    app.state.session_token = resolved_session_token
    app.state.bootstrap_token_hash = (
        hashlib.sha256(bootstrap_token.encode("utf-8")).digest()
        if bootstrap_token
        else None
    )
    app.state.bootstrap_consumed = False
    app.state.bootstrap_lock = threading.Lock()

    def _apply_security_headers(request: Request, response: Any) -> Any:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        if request.url.path.startswith("/preview/"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = (
                "sandbox allow-scripts allow-forms; "
                "default-src 'self' data: blob:; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "font-src 'self' data:; "
                "connect-src 'none'; "
                "object-src 'none'; "
                "base-uri 'none'; "
                "frame-ancestors 'self'"
            )
        else:
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "font-src 'self'; "
                "manifest-src 'self'; "
                "worker-src 'self'; "
                "frame-src 'self'; "
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

    def _guard_response(request: Request, code: str, message: str) -> JSONResponse:
        response = JSONResponse(
            status_code=403,
            content={"error": {"code": code, "message": message}},
        )
        return _apply_security_headers(request, response)

    @app.middleware("http")
    async def local_request_guard(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if not _is_loopback_host(request.headers.get("host")):
            return _guard_response(
                request,
                "FORBIDDEN_HOST",
                "허용되지 않은 Host header입니다.",
            )

        if request.method.upper() not in SAFE_METHODS:
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            if origin and not _origin_matches_request(request, origin):
                return _guard_response(request, "FORBIDDEN_ORIGIN", "허용되지 않은 Origin입니다.")
            if (
                not origin
                and referer
                and not _origin_matches_request(request, referer)
            ):
                return _guard_response(request, "FORBIDDEN_REFERER", "허용되지 않은 Referer입니다.")
            if request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
                return _guard_response(request, "FORBIDDEN_ORIGIN", "교차 사이트 요청은 허용되지 않습니다.")

        protected = path.startswith("/api/") and path not in {
            "/api/health",
            "/api/session/bootstrap",
        }
        if protected:
            expected = str(request.app.state.session_token)
            supplied = (
                request.headers.get("x-appforge-token")
                or request.cookies.get(SESSION_COOKIE_NAME)
            )
            if not supplied or not secrets.compare_digest(supplied, expected):
                return _guard_response(
                    request,
                    "INVALID_SESSION_TOKEN",
                    "세션 token이 없거나 올바르지 않습니다.",
                )

        response = await call_next(request)
        return _apply_security_headers(request, response)

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
            "context": {"fields": [".".join(map(str, item.get("loc", []))) for item in exc.errors()]},
        }
        return JSONResponse(status_code=422, content={"error": error})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled web error")
        error = {
            "code": "INTERNAL_SERVER_ERROR",
            "title": "서버 오류가 발생했습니다",
            "message": "요청을 처리하는 중 예기치 못한 오류가 발생했습니다.",
            "action": "서버 로그를 확인한 뒤 웹앱을 다시 시작하세요.",
            "context": {},
        }
        return JSONResponse(status_code=500, content={"error": error})

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        if _uses_llm_bridge_driver(request):
            try:
                await run_in_threadpool(
                    request.app.state.llm_bridge_manager.ensure_running,
                    _bridge_url(request),
                )
            except llm_bridge.BridgeError:
                pass
        return resolved_manager.health()

    @app.post("/api/session/bootstrap")
    async def bootstrap_session(
        payload: BootstrapSessionRequest,
        request: Request,
    ) -> JSONResponse:
        expected_hash = request.app.state.bootstrap_token_hash
        supplied_hash = hashlib.sha256(payload.code.encode("utf-8")).digest()
        with request.app.state.bootstrap_lock:
            valid = (
                isinstance(expected_hash, bytes)
                and not request.app.state.bootstrap_consumed
                and secrets.compare_digest(supplied_hash, expected_hash)
            )
            if valid:
                request.app.state.bootstrap_consumed = True
        if not valid:
            return _guard_response(
                request,
                "INVALID_BOOTSTRAP_CODE",
                "부트스트랩 코드가 없거나 이미 사용되었습니다.",
            )
        response = JSONResponse({"ok": True})
        response.set_cookie(
            SESSION_COOKIE_NAME,
            str(request.app.state.session_token),
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    @app.post("/api/jobs", status_code=202)
    async def create_job(payload: CreateJobRequest) -> dict[str, Any]:
        return resolved_manager.create_job(
            payload.prompt,
            mode=payload.mode or "autonomous",
            pipeline_name=payload.pipeline,
            provider=payload.provider,
            model=payload.model,
            generation=payload.generation,
            pricing=payload.pricing,
        )

    @app.get("/api/jobs")
    async def list_jobs(
        limit: int = 20,
        cursor: str | None = None,
        archived: bool = False,
    ) -> dict[str, Any]:
        return resolved_manager.list_jobs(limit=limit, cursor=cursor, archived=archived)

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        return resolved_manager.get_job(job_id)

    @app.patch("/api/jobs/{job_id}")
    async def update_job(job_id: str, payload: UpdateJobRequest) -> dict[str, Any]:
        return resolved_manager.update_job_metadata(
            job_id,
            starred=payload.starred,
            archived=payload.archived,
        )

    @app.post("/api/jobs/{job_id}/rerun", status_code=202)
    async def rerun_job(job_id: str) -> dict[str, Any]:
        return resolved_manager.rerun_job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        return await run_in_threadpool(resolved_manager.cancel_job, job_id)

    @app.post("/api/jobs/{job_id}/revise", status_code=202)
    async def revise_job(job_id: str, payload: ReviseJobRequest) -> dict[str, Any]:
        return await run_in_threadpool(
            resolved_manager.revise_job,
            job_id,
            payload.request,
            mode=payload.mode or "autonomous",
            pipeline_name=payload.pipeline,
            provider=payload.provider,
            model=payload.model,
            generation=payload.generation,
            pricing=payload.pricing,
        )

    @app.post("/api/jobs/{job_id}/approve", status_code=202)
    async def approve_job(job_id: str) -> dict[str, Any]:
        return await run_in_threadpool(resolved_manager.approve_job, job_id)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: str, payload: RetryJobRequest | None = None) -> dict[str, Any]:
        return await run_in_threadpool(
            resolved_manager.retry_stage,
            job_id,
            payload.stage if payload else None,
        )

    @app.post("/api/session/end")
    async def end_session(request: Request) -> JSONResponse:
        # Revoke the current credential before scheduling process shutdown so
        # even the short shutdown window cannot reuse a copied cookie/header.
        request.app.state.session_token = secrets.token_urlsafe(32)
        request.app.state.bootstrap_consumed = True
        resolved_manager.shutdown()
        resolved_bridge_manager.shutdown()
        timer = threading.Timer(0.25, request.app.state.shutdown_callback)
        timer.daemon = True
        timer.start()
        response = JSONResponse(
            {
                "closing": True,
                "message": "세션을 종료합니다. 잠시 뒤 로컬 서버가 중지됩니다.",
            }
        )
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            secure=request.url.scheme == "https",
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str) -> FileResponse:
        path, filename = resolved_manager.download_path(job_id)
        return FileResponse(
            path,
            filename=filename,
            media_type="application/zip",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        last_id_raw = request.headers.get("last-event-id") or request.query_params.get("lastEventId")
        last_event_id = int(last_id_raw) if last_id_raw and last_id_raw.isdigit() else None
        subscriber = resolved_manager.subscribe_events(job_id, last_event_id=last_event_id)

        async def event_stream() -> AsyncIterator[str]:
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await run_in_threadpool(
                            subscriber.get,
                            True,
                            SSE_KEEPALIVE_SECONDS,
                        )
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    event_name = str(item.get("event") or "message")
                    lines = []
                    event_id = item.get("id")
                    if event_id is not None:
                        lines.append(f"id: {event_id}")
                    lines.append(f"event: {event_name}")
                    lines.append(f"data: {json.dumps(item, ensure_ascii=False)}")
                    yield "\n".join(lines) + "\n\n"
                    if event_name in {"job_completed", "job_failed", "job_cancelled"}:
                        break
            finally:
                resolved_manager.unsubscribe_events(job_id, subscriber)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/{job_id}/workspace/tree")
    async def workspace_tree(job_id: str) -> dict[str, Any]:
        return await run_in_threadpool(resolved_manager.workspace_tree, job_id)

    @app.get("/api/jobs/{job_id}/workspace/file")
    async def workspace_file(job_id: str, path: str) -> dict[str, Any]:
        return await run_in_threadpool(resolved_manager.workspace_file, job_id, path)

    @app.get("/api/jobs/{job_id}/artifacts")
    async def artifact_list(job_id: str) -> dict[str, Any]:
        return await run_in_threadpool(resolved_manager.artifact_list, job_id)

    @app.get("/api/jobs/{job_id}/artifacts/{name}")
    async def artifact_payload(job_id: str, name: str) -> dict[str, Any]:
        return await run_in_threadpool(resolved_manager.artifact_payload, job_id, name)

    @app.post("/api/jobs/{job_id}/preview/build")
    async def preview_build(job_id: str) -> dict[str, Any]:
        return await run_in_threadpool(resolved_manager.build_preview, job_id)

    @app.get("/preview/{job_id}/", include_in_schema=False)
    async def preview_index(job_id: str) -> FileResponse:
        return await _preview_response(job_id, "index.html")

    @app.get("/preview/{job_id}/{preview_path:path}", include_in_schema=False)
    async def preview_asset(job_id: str, preview_path: str) -> FileResponse:
        return await _preview_response(job_id, preview_path)

    async def _preview_response(job_id: str, preview_path: str) -> FileResponse:
        path = await run_in_threadpool(resolved_manager.preview_file, job_id, preview_path)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})

    def _bridge_url(request: Request) -> str:
        return str(request.app.state.web_config.llm_bridge_url)

    def _uses_llm_bridge_driver(request: Request) -> bool:
        driver = str(request.app.state.web_config.driver).casefold().strip()
        return driver == "auto" or driver in LLM_BRIDGE_DRIVER_ALIASES

    async def _bridge_call(
        request: Request,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        bridge_url = _bridge_url(request)
        try:
            return await run_in_threadpool(func, bridge_url, *args, **kwargs)
        except llm_bridge.BridgeError as exc:
            if exc.status_code != 0:
                raise
            await run_in_threadpool(
                request.app.state.llm_bridge_manager.ensure_running,
                bridge_url,
                exc,
            )
            return await run_in_threadpool(func, bridge_url, *args, **kwargs)

    @app.exception_handler(llm_bridge.BridgeError)
    async def bridge_error_handler(_request: Request, exc: llm_bridge.BridgeError) -> JSONResponse:
        status = 502 if exc.status_code == 0 else exc.status_code
        context = _public_bridge_context(exc)
        action = context.pop("action", "llm_bridge 서비스가 실행 중인지 확인하세요.")
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": "LLM_BRIDGE_ERROR",
                    "title": "LLM 브릿지 오류",
                    "message": _public_bridge_message(exc),
                    "action": action,
                    "context": context,
                }
            },
        )

    @app.get("/api/llm/providers")
    async def llm_providers(request: Request) -> dict[str, Any]:
        return await _bridge_call(request, llm_bridge.list_providers)

    @app.get("/api/llm/providers/{provider_id}/models")
    async def llm_provider_models(provider_id: str, request: Request) -> dict[str, Any]:
        return await _bridge_call(request, llm_bridge.provider_models, provider_id)

    @app.put("/api/llm/providers/{provider_id}")
    async def llm_upsert_provider(
        provider_id: str,
        payload: UpsertProviderRequest,
        request: Request,
    ) -> dict[str, Any]:
        return await _bridge_call(
            request,
            llm_bridge.upsert_provider,
            provider_id,
            api_key=payload.apiKey,
            clear_api_key=payload.clearApiKey,
            base_url_override=payload.baseURL,
            default_model=payload.defaultModel,
        )

    @app.delete("/api/llm/providers/{provider_id}")
    async def llm_delete_provider(provider_id: str, request: Request) -> dict[str, Any]:
        return await _bridge_call(request, llm_bridge.delete_provider, provider_id)

    @app.post("/api/llm/providers/{provider_id}/test")
    async def llm_test_provider(
        provider_id: str,
        payload: TestProviderRequest,
        request: Request,
    ) -> dict[str, Any]:
        return await _bridge_call(
            request,
            llm_bridge.test_provider,
            provider_id,
            api_key=payload.apiKey,
            base_url_override=payload.baseURL,
            model=payload.model,
        )

    @app.get("/api/llm/active")
    async def llm_active(request: Request) -> dict[str, Any]:
        return await _bridge_call(request, llm_bridge.get_active)

    @app.put("/api/llm/active")
    async def llm_set_active(payload: ActiveProviderRequest, request: Request) -> dict[str, Any]:
        return await _bridge_call(
            request,
            llm_bridge.set_active,
            payload.provider,
            payload.model,
        )

    @app.get("/api/llm/oauth/providers")
    async def llm_oauth_providers(request: Request) -> dict[str, Any]:
        return await _bridge_call(request, llm_bridge.oauth_providers)

    @app.post("/api/llm/oauth/start")
    async def llm_oauth_start(payload: OAuthStartRequest, request: Request) -> dict[str, Any]:
        return await _bridge_call(
            request,
            llm_bridge.oauth_start,
            provider=payload.provider,
            method=payload.method,
        )

    @app.get("/api/llm/oauth/poll/{provider}/{poll_id}")
    async def llm_oauth_poll(provider: str, poll_id: str, request: Request) -> dict[str, Any]:
        return await _bridge_call(request, llm_bridge.oauth_poll, provider, poll_id)

    @app.post("/api/llm/oauth/refresh/{provider}")
    async def llm_oauth_refresh(provider: str, request: Request) -> dict[str, Any]:
        return await _bridge_call(request, llm_bridge.oauth_refresh, provider)

    @app.post("/api/llm/quick-connect")
    async def llm_quick_connect(payload: QuickConnectRequest, request: Request) -> dict[str, Any]:
        """One-shot connect: save key → test → activate. Mirrors `appforge auth login`."""
        try:
            await _bridge_call(
                request,
                llm_bridge.upsert_provider,
                payload.provider,
                api_key=payload.apiKey,
                base_url_override=payload.baseURL,
                default_model=payload.model,
            )
        except llm_bridge.BridgeError as exc:
            return {"ok": False, "step": "save", "error": _public_bridge_message(exc), "provider": payload.provider}
        try:
            test_result = await _bridge_call(
                request,
                llm_bridge.test_provider,
                payload.provider,
                api_key=payload.apiKey,
                base_url_override=payload.baseURL,
                model=payload.model,
                timeout=30.0,
            )
        except llm_bridge.BridgeError as exc:
            return {"ok": False, "step": "test", "error": _public_bridge_message(exc), "provider": payload.provider}
        if not test_result.get("ok"):
            return {
                "ok": False,
                "step": "test",
                "error": test_result.get("error") or "연결 테스트 실패",
                "provider": payload.provider,
                "model": test_result.get("model"),
                "test": test_result,
            }
        chosen_model = payload.model or test_result.get("model")
        try:
            await _bridge_call(
                request,
                llm_bridge.set_active,
                payload.provider,
                chosen_model,
            )
        except llm_bridge.BridgeError as exc:
            return {"ok": False, "step": "activate", "error": _public_bridge_message(exc), "provider": payload.provider, "test": test_result}
        return {
            "ok": True,
            "step": "done",
            "provider": payload.provider,
            "model": chosen_model,
            "test": test_result,
        }

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


def _request_process_shutdown() -> None:
    os.kill(os.getpid(), signal.SIGINT)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
    log_level: str = "info",
) -> None:
    if host.casefold() not in LOCAL_HOSTS:
        raise ValueError("AppForge Web UI는 loopback 주소에만 바인딩할 수 있습니다.")
    config = WebConfig.from_env()
    session_token = secrets.token_urlsafe(32)
    bootstrap_token = secrets.token_urlsafe(32)
    app = create_app(
        config,
        session_token=session_token,
        bootstrap_token=bootstrap_token,
    )
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    base_url = f"http://{url_host}:{port}/"
    browser_url = f"{base_url}#bootstrap={bootstrap_token}"
    logger.info("Open AppForge Web UI: %s", base_url)
    if open_browser:
        timer = threading.Timer(0.8, lambda: webbrowser.open(browser_url))
        timer.daemon = True
        timer.start()
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="appforge-web",
        description="Run the AppForge-LLM v7 autonomous AI app builder web interface.",
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
