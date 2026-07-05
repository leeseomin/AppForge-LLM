from __future__ import annotations

import copy
import json
import os
import queue
import re
import shutil
import threading
import traceback
import uuid
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import __version__
from .drivers import DriverError, create_driver
from .constants import IGNORED_DIRS, PROJECT_FILE_NAME, STATE_FILE_NAME, STAGE_RESULT_FILE_NAME
from .models import PipelineSpec, ProjectLayout, StageSpec
from .pipelines import all_pipelines, auto_select_pipeline, list_pipeline_names, load_pipeline, select_pipeline
from .projects import initialize_project
from .runner import PipelineRunner
from .tooling.tools.quality import RunBuildTool
from .tooling.tools.release import ArchiveWorkspaceTool
from .util import (
    atomic_write_json,
    atomic_write_text,
    read_json,
    redact,
    slugify,
    safe_resolve,
    truncate,
    utc_now,
)

QUEUED_JOB_STATUSES = {"queued"}
RUNNING_JOB_STATUSES = {"initializing", "running", "packaging"}
AWAITING_JOB_STATUSES = {"awaiting_approval"}
ACTIVE_JOB_STATUSES = QUEUED_JOB_STATUSES | RUNNING_JOB_STATUSES | AWAITING_JOB_STATUSES
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
MAX_EVENTS = 160
_UNSET = object()


STAGE_COPY: dict[str, tuple[str, str]] = {
    "intake": ("요청 정리", "목표, 사용자, 제약 조건과 안전한 기본값을 정리합니다."),
    "specification": ("기능 명세", "구현 가능한 요구사항과 완료 기준을 확정합니다."),
    "repository_analysis": ("기존 코드 분석", "저장소 구조와 현재 동작, 변경 영향을 분석합니다."),
    "reproduce": ("문제 재현", "오류를 재현하고 기준 동작과 증거를 확보합니다."),
    "diagnosis": ("원인 진단", "근본 원인과 영향 범위를 식별합니다."),
    "change_plan": ("변경 계획", "최소 변경 범위와 회귀 방지 방법을 정합니다."),
    "prototype_plan": ("프로토타입 계획", "핵심 가설과 가장 작은 구현 범위를 정합니다."),
    "engineering_spec": ("경량 엔지니어링 설계", "소형 요청용 요구사항, 화면, 상태와 검증 기준을 한 번에 정리합니다."),
    "workflow_design": ("워크플로 엔지니어링", "상태, 트리거, 예외, 재시도와 복구 흐름을 설계합니다."),
    "memory_engineering": ("메모리 엔지니어링", "저장 상태, 세션, 캐시, 감사 기록과 복구 규칙을 설계합니다."),
    "loop_engineering": ("루프 엔지니어링", "재시도, 폴링, 작업자, 검증 루프의 종료 조건과 안전장치를 설계합니다."),
    "api_design": ("API 설계", "엔드포인트, 오류, 인증 및 호환성 계약을 설계합니다."),
    "data_contract": ("데이터 계약", "입력, 출력, 품질 규칙과 스키마를 정의합니다."),
    "data_model": ("데이터 모델", "엔터티, 관계, 무결성과 마이그레이션을 설계합니다."),
    "architecture": ("구조 설계", "가장 단순하고 일관된 기술 구조를 결정합니다."),
    "experience": ("UX 설계", "주요 화면, 상태, 오류, 반응형 및 접근성을 설계합니다."),
    "implementation": ("앱 구현", "소스 코드, 테스트와 실행 문서를 완성합니다."),
    "fix": ("수정 구현", "원인을 제거하고 관련 테스트를 추가합니다."),
    "verification": ("테스트·빌드", "자동 테스트와 빌드를 실행해 완료 기준을 검증합니다."),
    "regression": ("회귀 검증", "수정된 동작과 기존 기능의 회귀 여부를 확인합니다."),
    "data_quality": ("데이터 품질", "정확성, 누락, 중복과 이상치를 검증합니다."),
    "compatibility": ("호환성 확인", "지원 환경과 이전 버전 호환성을 확인합니다."),
    "security": ("보안 점검", "비밀정보, 의존성 및 주요 위협을 검사합니다."),
    "operations": ("운영 준비", "관측성, 복구, 설정과 운영 절차를 준비합니다."),
    "demo": ("동작 확인", "핵심 사용자 흐름을 실행해 결과를 확인합니다."),
    "packaging": ("패키징", "설치 가능한 배포 산출물과 안내를 준비합니다."),
    "release": ("릴리스 점검", "재현 가능한 빌드와 릴리스 준비 상태를 확인합니다."),
    "handoff": ("인계 준비", "소스, 빠른 시작법과 검증 증거를 한데 모읍니다."),
}

ERROR_TITLES = {
    "AGENT_NOT_AVAILABLE": "LLM 실행 환경을 찾을 수 없습니다",
    "DRIVER_ERROR": "LLM 실행 환경을 시작하지 못했습니다",
    "AGENT_PROCESS_FAILED": "LLM 실행이 실패했습니다",
    "STAGE_RESULT_INVALID": "단계 완료 기록이 올바르지 않습니다",
    "STAGE_CHECK_FAILED": "필수 검증을 통과하지 못했습니다",
    "STAGE_REVIEW_FAILED": "단계 검토를 통과하지 못했습니다",
    "STAGE_FAILED": "단계 실행이 실패했습니다",
    "REPEATED_FAILURE_LOOP": "반복 실패 루프를 감지했습니다",
    "HUMAN_APPROVAL_REQUIRED": "승인이 필요한 상태입니다",
    "PROJECT_INITIALIZATION_FAILED": "프로젝트를 준비하지 못했습니다",
    "ARCHIVE_CREATION_FAILED": "다운로드 ZIP을 만들지 못했습니다",
    "ARCHIVE_INVALID": "생성된 ZIP 파일이 손상되었습니다",
    "UNEXPECTED_ERROR": "예상하지 못한 오류가 발생했습니다",
    "SERVER_RESTARTED": "실행 중 서버가 다시 시작되었습니다",
    "ARCHIVE_MISSING": "완료된 ZIP 파일을 찾을 수 없습니다",
    "JOB_CANCELLED": "작업이 취소되었습니다",
}


class WebJobError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        action: str = "",
        status_code: int = 400,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action
        self.status_code = status_code
        self.context = context or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": ERROR_TITLES.get(self.code, "요청을 처리하지 못했습니다"),
            "message": self.message,
            "action": self.action,
            "context": self.context,
        }


DEFAULT_LLM_BRIDGE_URL = "http://127.0.0.1:8788"
LLM_BRIDGE_DRIVER_ALIASES = {"llm-bridge", "llm_bridge", "llm", "llm-bridge-agent", "llm_bridge_agent", "llm-agent", "agent"}


@dataclass(frozen=True)
class WebConfig:
    projects_dir: Path = field(default_factory=lambda: Path("projects"))
    data_dir: Path = field(default_factory=lambda: Path(".appforge-web"))
    driver: str = "llm-bridge-agent"
    model: str | None = None
    allow_network: bool = False
    allow_destructive: bool = False
    unsafe_agent: bool = False
    max_stage_attempts: int | None = None
    stage_timeout: int = 3600
    max_turns: int | None = None
    prompt_max_chars: int = 20_000
    llm_bridge_url: str = DEFAULT_LLM_BRIDGE_URL
    llm_provider: str | None = None
    llm_router: bool = False
    router_model: str | None = None
    router_timeout: int = 30
    queue_limit: int = 8

    @classmethod
    def from_env(cls) -> "WebConfig":
        return cls(
            projects_dir=Path(os.environ.get("APPFORGE_PROJECTS_DIR", "projects")),
            data_dir=Path(os.environ.get("APPFORGE_DATA_DIR", ".appforge-web")),
            driver=os.environ.get("APPFORGE_DRIVER", "llm-bridge-agent"),
            model=os.environ.get("APPFORGE_MODEL") or None,
            allow_network=_env_bool("APPFORGE_ALLOW_NETWORK", False),
            allow_destructive=_env_bool("APPFORGE_ALLOW_DESTRUCTIVE", False),
            unsafe_agent=_env_bool("APPFORGE_UNSAFE_AGENT", False),
            max_stage_attempts=_env_optional_int("APPFORGE_MAX_STAGE_ATTEMPTS"),
            stage_timeout=_env_int("APPFORGE_STAGE_TIMEOUT", 3600, minimum=60),
            max_turns=_env_optional_int("APPFORGE_MAX_TURNS"),
            prompt_max_chars=_env_int("APPFORGE_PROMPT_MAX_CHARS", 20_000, minimum=100),
            llm_bridge_url=os.environ.get("APPFORGE_LLM_BRIDGE_URL", DEFAULT_LLM_BRIDGE_URL),
            llm_provider=os.environ.get("APPFORGE_LLM_PROVIDER") or None,
            llm_router=_env_bool("APPFORGE_LLM_ROUTER", True),
            router_model=os.environ.get("APPFORGE_ROUTER_MODEL") or None,
            router_timeout=_env_int("APPFORGE_ROUTER_TIMEOUT", 30, minimum=5),
            queue_limit=_env_int("APPFORGE_QUEUE_LIMIT", 8, minimum=1),
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false, 1/0, yes/no, or on/off")


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed


def _env_optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{name} must be at least 1")
    return parsed


class JobManager:
    def __init__(self, config: WebConfig) -> None:
        self.config = config
        self.projects_dir = config.projects_dir.expanduser().resolve()
        self.data_dir = config.data_dir.expanduser().resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._event_subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}
        self._load_jobs()
        with self._lock:
            self._maybe_start_next_job_locked()

    def health(self) -> dict[str, Any]:
        readiness = self.driver_readiness()
        with self._lock:
            active_job_id = self._active_job_id_locked()
            queue_depth = sum(1 for job in self._jobs.values() if job.get("status") == "queued")
            running_job_id = self._running_job_id_locked()
        return {
            "status": "ready" if readiness["ready"] else "needs_setup",
            "ready": readiness["ready"],
            "version": __version__,
            "driver": readiness,
            "busy": running_job_id is not None,
            "active_job_id": active_job_id,
            "running_job_id": running_job_id,
            "queue_depth": queue_depth,
            "queue_limit": self.config.queue_limit,
            "network_enabled": self.config.allow_network,
            "prompt_max_chars": self.config.prompt_max_chars,
            "safety": {
                "deployment_enabled": False,
                "destructive_operations_enabled": self.config.allow_destructive,
            },
        }

    def driver_readiness(self) -> dict[str, Any]:
        requested = self.config.driver.casefold().strip()
        if requested in LLM_BRIDGE_DRIVER_ALIASES:
            return self._llm_bridge_readiness()
        if requested == "auto":
            bridge_readiness = self._llm_bridge_readiness()
            return {
                **bridge_readiness,
                "requested": "auto",
            }
        if requested in {"codex", "claude"}:
            return {
                "ready": False,
                "requested": requested,
                "selected": None,
                "label": "CLI 드라이버 제거됨",
                "message": f"{requested} CLI 드라이버는 더 이상 지원하지 않습니다.",
                "action": (
                    "LLM 연결 설정에서 외부 프로바이더 API 키를 저장하고 "
                    "APPFORGE_DRIVER=llm-bridge-agent로 실행하세요."
                ),
            }
        return {
            "ready": False,
            "requested": requested,
            "selected": None,
            "label": "알 수 없는 실행기",
            "message": f"지원하지 않는 드라이버입니다: {self.config.driver}",
            "action": "APPFORGE_DRIVER를 auto, llm-bridge-agent 또는 llm-bridge로 설정하세요.",
        }

    def _llm_bridge_readiness(self) -> dict[str, Any]:
        from . import llm_bridge

        bridge_url = self.config.llm_bridge_url
        selected_driver = self.config.driver.casefold().strip()
        if selected_driver == "auto":
            selected_driver = "llm-bridge-agent"
        if selected_driver not in LLM_BRIDGE_DRIVER_ALIASES:
            selected_driver = "llm-bridge-agent"
        try:
            llm_bridge.ping(bridge_url)
            active = llm_bridge.get_active(bridge_url)
            provider_payload = llm_bridge.list_providers(bridge_url)
        except llm_bridge.BridgeError as exc:
            return {
                "ready": False,
                "requested": selected_driver,
                "selected": None,
                "label": "LLM 브릿지",
                "message": str(exc),
                "action": "llm_bridge 폴더에서 `bun install` 후 `bun run dev` 로 브릿지를 시작하세요.",
            }
        provider = active.get("provider") or self.config.llm_provider
        model = active.get("model") or self.config.model
        if not provider:
            return {
                "ready": False,
                "requested": selected_driver,
                "selected": None,
                "label": "LLM 브릿지",
                "message": "브릿지는 실행 중이지만 활성 프로바이더가 없습니다.",
                "action": "설정 패널에서 프로바이더와 모델을 선택하세요.",
            }
        statuses = provider_payload.get("providers", [])
        provider_status = next(
            (
                item
                for item in statuses
                if isinstance(item, dict) and item.get("id") == provider
            ),
            None,
        )
        if not provider_status:
            return {
                "ready": False,
                "requested": selected_driver,
                "selected": None,
                "label": "LLM 브릿지",
                "message": f"알 수 없는 프로바이더입니다: {provider}",
                "action": "설정 패널에서 지원되는 프로바이더를 선택하세요.",
            }
        if not provider_status.get("configured"):
            return {
                "ready": False,
                "requested": selected_driver,
                "selected": None,
                "label": "LLM 브릿지",
                "message": f"{provider} 프로바이더 설정이 완료되지 않았습니다.",
                "action": "설정 패널에서 API 키와 필요한 Base URL을 저장하세요.",
            }
        return {
            "ready": True,
            "requested": selected_driver,
            "selected": selected_driver,
            "label": f"LLM 브릿지 · {provider}",
            "message": f"{provider}/{model or '기본 모델'} 을(를) 사용합니다.",
            "action": "",
        }

    def _validate_prompt(self, prompt: str, *, field_name: str = "요청") -> str:
        normalized = prompt.strip()
        if not normalized:
            raise WebJobError(
                "EMPTY_PROMPT",
                f"{field_name} 내용을 입력하세요.",
                action="한 문장 이상으로 원하는 결과를 설명해 주세요.",
                status_code=422,
            )
        if len(normalized) > self.config.prompt_max_chars:
            raise WebJobError(
                "PROMPT_TOO_LONG",
                f"요청은 최대 {self.config.prompt_max_chars:,}자까지 입력할 수 있습니다.",
                action="중복 설명을 줄이거나 별도 요구사항을 핵심 기준으로 압축해 주세요.",
                status_code=422,
                context={"max_chars": self.config.prompt_max_chars},
            )
        return normalized

    def _route_initial_prompt(
        self,
        prompt: str,
        *,
        pipeline_name: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if pipeline_name:
            load_pipeline(pipeline_name)
            return pipeline_name, {
                "source": "manual",
                "pipeline": pipeline_name,
                "confidence": 1.0,
                "complexity": "standard",
                "rationale": "사용자가 시작 전에 파이프라인을 명시했습니다.",
            }

        bridge_url = self.config.llm_bridge_url if self.config.llm_router else None
        selected, routing = select_pipeline(
            prompt,
            existing_repo=False,
            bridge_url=bridge_url,
            provider=self.config.llm_provider,
            model=self.config.router_model or self.config.model,
            timeout=self.config.router_timeout,
        )
        routing = dict(routing)
        routing.setdefault("pipeline", selected)
        routing.setdefault("source", "keyword_fallback")
        routing.setdefault("rationale", "키워드/복잡도 폴백 라우터가 파이프라인을 선택했습니다.")
        if not self.config.llm_router:
            routing["source"] = "keyword-fallback"
            routing["rationale"] = "LLM 라우터가 비활성화되어 키워드/복잡도 폴백을 사용했습니다."
        return selected, routing

    def _choose_revision_pipeline(self, request: str) -> str:
        text = request.casefold()
        bug_words = ("bug", "버그", "오류", "에러", "fix", "고쳐", "깨져", "crash", "exception", "회귀")
        return "bugfix" if any(word in text for word in bug_words) else "feature"

    def _revision_prompt(
        self,
        *,
        parent_prompt: str,
        request: str,
        parent_job_id: str,
        revision_index: int,
    ) -> str:
        return (
            "# Revision request for an existing AppForge workspace\n\n"
            f"Parent job: `{parent_job_id}`\n"
            f"Revision: #{revision_index}\n\n"
            "## Original app request\n"
            f"{parent_prompt.strip()}\n\n"
            "## Requested change\n"
            f"{request.strip()}\n\n"
            "## Rules\n"
            "- Reuse the existing workspace. Do not start a new unrelated app.\n"
            "- Read existing files before editing them.\n"
            "- Make the smallest safe change that satisfies the requested revision.\n"
            "- Preserve unrelated behavior and update tests/docs when relevant.\n"
        )

    def _new_job_record(
        self,
        *,
        job_id: str,
        prompt: str,
        pipeline: PipelineSpec,
        mode: str,
        routing: dict[str, Any],
        created_at: str,
        parent_job_id: str | None = None,
        revision_index: int | None = None,
        revision_request: str | None = None,
        workspace_ref: str | None = None,
        project_path: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": job_id,
            "version": "2.0",
            "prompt": prompt,
            "status": "queued",
            "message": "실행을 준비하고 있습니다.",
            "created_at": created_at,
            "updated_at": created_at,
            "started_at": None,
            "completed_at": None,
            "pipeline": pipeline.name,
            "pipeline_description": pipeline.description,
            "routing": routing,
            "mode": mode,
            "auto_approve": mode == "autonomous",
            "parent_job_id": parent_job_id,
            "revision_index": revision_index,
            "revision_request": revision_request,
            "workspace_ref": workspace_ref,
            "resume_existing": False,
            "only_stage": None,
            "driver": None,
            "project_name": Path(project_path).name if project_path else None,
            "project_path": project_path,
            "active_stage": "preflight",
            "stages": self._initial_stages(pipeline),
            "events": [],
            "error": None,
            "archive_path": None,
            "download": {
                "available": False,
                "url": None,
                "filename": None,
                "size_bytes": None,
            },
            "preview": {
                "available": False,
                "url": None,
                "path": None,
                "built_at": None,
            },
        }

    def create_job(
        self,
        prompt: str,
        *,
        mode: str = "autonomous",
        pipeline_name: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._validate_prompt(prompt)
        run_mode = _normalize_job_mode(mode)
        selected, routing = self._route_initial_prompt(normalized, pipeline_name=pipeline_name)
        pipeline = load_pipeline(selected)
        job_id = uuid.uuid4().hex
        now = utc_now()
        job = self._new_job_record(
            job_id=job_id,
            prompt=normalized,
            pipeline=pipeline,
            mode=run_mode,
            routing=routing,
            created_at=now,
        )
        with self._lock:
            self._ensure_queue_capacity_locked()
            self._jobs[job_id] = job
            self._cancel_events[job_id] = threading.Event()
            self._record_event_locked(job, "job_queued", "작업이 실행 대기열에 등록되었습니다.")
            self._save_locked(job)
            self._maybe_start_next_job_locked()
            return self._public_job_locked(job)

    def revise_job(
        self,
        parent_job_id: str,
        request: str,
        *,
        mode: str = "autonomous",
        pipeline_name: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._validate_prompt(request, field_name="수정 요청")
        run_mode = _normalize_job_mode(mode)
        with self._lock:
            parent = self._jobs.get(parent_job_id)
            if parent is None:
                raise WebJobError("JOB_NOT_FOUND", "수정할 원본 작업을 찾을 수 없습니다.", status_code=404)
            project_path = parent.get("project_path")
            if not project_path:
                raise WebJobError(
                    "PROJECT_NOT_READY",
                    "원본 작업공간이 아직 준비되지 않아 수정 작업을 만들 수 없습니다.",
                    action="원본 작업이 프로젝트 준비 단계를 지난 뒤 다시 요청하세요.",
                    status_code=409,
                )
            parent_prompt = str(parent.get("prompt") or "")
            revision_index = 1 + max(
                [
                    int(job.get("revision_index") or 0)
                    for job in self._jobs.values()
                    if job.get("parent_job_id") == parent_job_id
                ]
                or [0]
            )
        selected = pipeline_name or self._choose_revision_pipeline(normalized)
        pipeline = load_pipeline(selected)
        revision_prompt = self._revision_prompt(
            parent_prompt=parent_prompt,
            request=normalized,
            parent_job_id=parent_job_id,
            revision_index=revision_index,
        )
        routing = {
            "source": "revision",
            "pipeline": selected,
            "confidence": 0.92,
            "complexity": "standard",
            "rationale": "기존 작업공간에 대한 후속 수정이므로 feature/bugfix 파이프라인을 사용합니다.",
            "requested_pipeline": pipeline_name,
        }
        job_id = uuid.uuid4().hex
        now = utc_now()
        job = self._new_job_record(
            job_id=job_id,
            prompt=revision_prompt,
            pipeline=pipeline,
            mode=run_mode,
            routing=routing,
            created_at=now,
            parent_job_id=parent_job_id,
            revision_index=revision_index,
            revision_request=normalized,
            workspace_ref=str(project_path),
            project_path=None,
        )
        job["message"] = f"수정 #{revision_index} 작업이 실행 대기열에 등록되었습니다."
        with self._lock:
            self._ensure_queue_capacity_locked()
            self._jobs[job_id] = job
            parent_record = self._jobs.get(parent_job_id)
            if parent_record is not None:
                children = parent_record.setdefault("children", [])
                if job_id not in children:
                    children.append(job_id)
                parent_record["updated_at"] = utc_now()
                self._save_locked(parent_record)
            self._cancel_events[job_id] = threading.Event()
            self._record_event_locked(
                job,
                "revision_queued",
                f"원본 작업 아래 수정 #{revision_index} 작업이 등록되었습니다.",
                data={"parent_job_id": parent_job_id, "revision_index": revision_index},
            )
            self._save_locked(job)
            self._maybe_start_next_job_locked()
            return self._public_job_locked(job)

    def approve_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require_job_locked(job_id)
            if job.get("status") != "awaiting_approval":
                raise WebJobError(
                    "APPROVAL_NOT_AVAILABLE",
                    "승인을 기다리는 작업이 아닙니다.",
                    action="현재 상태를 새로고침한 뒤 다시 확인하세요.",
                    status_code=409,
                    context={"status": job.get("status")},
                )
            job["status"] = "queued"
            job["message"] = "승인이 기록되어 다음 단계 실행을 대기하고 있습니다."
            job["updated_at"] = utc_now()
            job["resume_existing"] = True
            job["auto_approve"] = True
            job["approval"] = {"approved_at": utc_now(), "stage": job.get("active_stage")}
            self._cancel_events[job_id] = threading.Event()
            self._record_event_locked(job, "job_approved", "승인이 기록되어 파이프라인을 계속합니다.")
            self._save_locked(job)
            self._maybe_start_next_job_locked()
            return self._public_job_locked(job)

    def retry_stage(self, job_id: str, stage: str | None = None) -> dict[str, Any]:
        with self._lock:
            job = self._require_job_locked(job_id)
            project_path = job.get("project_path")
            if not project_path:
                raise WebJobError("PROJECT_NOT_READY", "재시도할 작업공간이 없습니다.", status_code=409)
            stage_id = stage or (job.get("error") or {}).get("stage") or job.get("active_stage")
            if not stage_id:
                raise WebJobError(
                    "STAGE_NOT_FOUND",
                    "재시도할 단계를 결정하지 못했습니다.",
                    action="오류 세부정보에 표시된 단계에서 다시 시도하세요.",
                    status_code=422,
                )
            if job.get("status") in RUNNING_JOB_STATUSES:
                raise WebJobError("JOB_ALREADY_RUNNING", "이미 실행 중인 작업입니다.", status_code=409)
            job["status"] = "queued"
            job["message"] = f"{self._stage_title(str(stage_id))} 단계부터 재시도하도록 대기열에 등록했습니다."
            job["active_stage"] = str(stage_id)
            job["updated_at"] = utc_now()
            job["completed_at"] = None
            job["error"] = None
            job["resume_existing"] = True
            job["auto_approve"] = True
            job["only_stage"] = str(stage_id)
            stage_record = self._find_stage_locked(job, str(stage_id))
            if stage_record is not None:
                stage_record["status"] = "retrying"
                stage_record["detail"] = "사용자 요청으로 이 단계부터 자동 수리를 다시 시도합니다."
                stage_record["completed_at"] = None
                stage_record["error"] = None
            self._cancel_events[job_id] = threading.Event()
            self._record_event_locked(job, "stage_retry_requested", "사용자가 단계 재시도를 요청했습니다.", data={"stage": stage_id})
            self._save_locked(job)
            self._maybe_start_next_job_locked()
            return self._public_job_locked(job)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise WebJobError(
                    "JOB_NOT_FOUND",
                    "요청한 작업을 찾을 수 없습니다.",
                    action="새 요청을 시작하거나 올바른 작업 주소인지 확인하세요.",
                    status_code=404,
                )
            return self._public_job_locked(job)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise WebJobError(
                    "JOB_NOT_FOUND",
                    "요청한 작업을 찾을 수 없습니다.",
                    action="새 요청을 시작하거나 올바른 작업 주소인지 확인하세요.",
                    status_code=404,
                )
            if job.get("status") not in (ACTIVE_JOB_STATUSES | AWAITING_JOB_STATUSES):
                return self._public_job_locked(job)
            cancel_event = self._cancel_events.setdefault(job_id, threading.Event())
            cancel_event.set()
            stage = str(job.get("active_stage") or "") or None
            error = self._make_error(
                "JOB_CANCELLED",
                "사용자가 실행 중인 작업을 취소했습니다.",
                action="필요하면 새 요청을 시작하세요.",
                stage=stage,
            )
            self._mark_cancelled_locked(job, error, stage=stage)
            return self._public_job_locked(job)

    def download_path(self, job_id: str) -> tuple[Path, str]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise WebJobError(
                    "JOB_NOT_FOUND",
                    "요청한 작업을 찾을 수 없습니다.",
                    status_code=404,
                )
            if job.get("status") != "completed" or not job.get("archive_path"):
                raise WebJobError(
                    "DOWNLOAD_NOT_READY",
                    "ZIP 파일은 모든 단계가 완료된 뒤 다운로드할 수 있습니다.",
                    action="현재 단계가 완료될 때까지 상태 화면을 확인하세요.",
                    status_code=409,
                )
            path = Path(str(job["archive_path"])).expanduser().resolve()
            project_path = Path(str(job.get("project_path") or "")).expanduser().resolve()
            reports_dir = (project_path / ".appforge" / "reports").resolve()
            if (
                not path.is_file()
                or path.suffix.casefold() != ".zip"
                or reports_dir not in path.parents
            ):
                raise WebJobError(
                    "ARCHIVE_MISSING",
                    "완료 기록은 있지만 다운로드 ZIP 파일을 찾을 수 없습니다.",
                    action="같은 요청으로 다시 실행하고, 반복되면 서버 로그를 확인하세요.",
                    status_code=410,
                )
            return path, str(job["download"]["filename"] or path.name)

    def shutdown(self) -> None:
        with self._lock:
            active_jobs = [
                job
                for job in self._jobs.values()
                if job.get("status") in ACTIVE_JOB_STATUSES
            ]
            for job in active_jobs:
                job_id = str(job["id"])
                cancel_event = self._cancel_events.setdefault(job_id, threading.Event())
                cancel_event.set()
                stage = str(job.get("active_stage") or "") or None
                error = self._make_error(
                    "JOB_CANCELLED",
                    "세션 종료로 실행 중인 작업을 취소했습니다.",
                    action="앱을 다시 시작한 뒤 필요하면 새 요청을 실행하세요.",
                    stage=stage,
                )
                self._mark_cancelled_locked(job, error, stage=stage)

    def subscribe_events(self, job_id: str) -> queue.Queue[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise WebJobError("JOB_NOT_FOUND", "작업을 찾을 수 없습니다.", status_code=404)
            q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=200)
            self._event_subscribers.setdefault(job_id, []).append(q)
            q.put({"event": "snapshot", "message": "현재 작업 상태", "timestamp": utc_now(), "job": self._public_job_locked(job)})
            return q

    def unsubscribe_events(self, job_id: str, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subscribers = self._event_subscribers.get(job_id)
            if not subscribers:
                return
            try:
                subscribers.remove(subscriber)
            except ValueError:
                return
            if not subscribers:
                self._event_subscribers.pop(job_id, None)

    def workspace_tree(self, job_id: str, *, max_depth: int = 8, max_entries: int = 2500) -> dict[str, Any]:
        layout = self._job_layout(job_id)
        entries: list[dict[str, Any]] = []
        root = layout.root.resolve()
        truncated = False
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            rel_dir = current_path.relative_to(root)
            depth = len(rel_dir.parts)
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".git"))
            if depth >= max_depth:
                dirs[:] = []
            for dirname in dirs:
                rel = (rel_dir / dirname).as_posix() if rel_dir != Path(".") else dirname
                entries.append({"path": f"{rel}/", "type": "directory"})
                if len(entries) >= max_entries:
                    truncated = True
                    break
            if truncated:
                break
            for filename in sorted(files):
                if filename == ".DS_Store":
                    continue
                path = current_path / filename
                if path.is_symlink():
                    continue
                rel = (rel_dir / filename).as_posix() if rel_dir != Path(".") else filename
                entries.append({"path": rel, "type": "file", "size": path.stat().st_size if path.exists() else None})
                if len(entries) >= max_entries:
                    truncated = True
                    break
            if truncated:
                break
        return {"root": str(root), "entries": entries, "truncated": truncated}

    def workspace_file(self, job_id: str, relative_path: str) -> dict[str, Any]:
        layout = self._job_layout(job_id)
        path = self._safe_workspace_read_path(layout, relative_path)
        raw = path.read_bytes()
        if len(raw) > 512_000:
            raise WebJobError("FILE_TOO_LARGE", "512KB를 넘는 파일은 웹에서 바로 열 수 없습니다.", status_code=413)
        if b"\x00" in raw[:8192]:
            raise WebJobError("BINARY_FILE", "바이너리 파일은 코드 뷰어에서 열 수 없습니다.", status_code=415)
        text = raw.decode("utf-8", errors="replace")
        return {"path": path.relative_to(layout.root).as_posix(), "content": text, "size": len(raw)}

    def artifact_payload(self, job_id: str, name: str) -> dict[str, Any]:
        layout = self._job_layout(job_id)
        safe_name = slugify(name, fallback="artifact").replace("-", "_")
        path = layout.artifacts / f"{safe_name}.json"
        if not path.is_file():
            raise WebJobError("ARTIFACT_NOT_FOUND", f"아티팩트를 찾을 수 없습니다: {name}", status_code=404)
        data = read_json(path)
        return {"name": safe_name, "path": str(path), "payload": data}

    def artifact_list(self, job_id: str) -> dict[str, Any]:
        layout = self._job_layout(job_id)
        artifacts: list[dict[str, Any]] = []
        for path in sorted(layout.artifacts.glob("*.json")):
            try:
                payload = read_json(path)
            except Exception:
                payload = None
            artifacts.append({
                "name": path.stem,
                "path": str(path),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "updated_at": _mtime_iso(path),
                "summary": _artifact_summary(payload),
            })
        return {"artifacts": artifacts}

    def build_preview(self, job_id: str) -> dict[str, Any]:
        layout = self._job_layout(job_id)
        result = RunBuildTool().run(layout.root, {"allow_network": self.config.allow_network, "timeout": self.config.stage_timeout})
        candidates = [
            layout.root / "dist",
            layout.root / "build",
            layout.root / "frontend" / "dist",
            layout.root / "client" / "dist",
            layout.root / "app" / "dist",
        ]
        dist = next((path for path in candidates if (path / "index.html").is_file()), None)
        if dist is None:
            raise WebJobError(
                "PREVIEW_NOT_AVAILABLE",
                "정적 프리뷰 산출물(index.html 포함 dist/build)을 찾지 못했습니다.",
                action="프로젝트의 build 명령과 출력 폴더를 확인하세요.",
                status_code=409,
                context={"build_result": result.to_dict()},
            )
        with self._lock:
            job = self._require_job_locked(job_id)
            preview = {"available": True, "url": f"/preview/{job_id}/", "path": str(dist.resolve()), "built_at": utc_now()}
            job["preview"] = preview
            self._record_event_locked(job, "preview_built", "정적 프리뷰가 준비되었습니다.", data={"url": preview["url"]})
            self._save_locked(job)
            return copy.deepcopy(preview)

    def preview_file(self, job_id: str, relative_path: str) -> Path:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise WebJobError("JOB_NOT_FOUND", "작업을 찾을 수 없습니다.", status_code=404)
            preview = job.get("preview") or {}
            root_value = preview.get("path")
        if not root_value:
            raise WebJobError("PREVIEW_NOT_READY", "프리뷰를 먼저 빌드해야 합니다.", status_code=409)
        root = Path(str(root_value)).expanduser().resolve()
        target = safe_resolve(root, relative_path or "index.html")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # SPA fallback for client-side routing.
            fallback = root / "index.html"
            if fallback.is_file():
                return fallback
            raise WebJobError("PREVIEW_FILE_NOT_FOUND", "요청한 프리뷰 파일을 찾을 수 없습니다.", status_code=404)
        return target

    def _job_layout(self, job_id: str) -> ProjectLayout:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise WebJobError("JOB_NOT_FOUND", "작업을 찾을 수 없습니다.", status_code=404)
            project_path = job.get("project_path")
        if not project_path:
            raise WebJobError("PROJECT_NOT_READY", "프로젝트 작업공간이 아직 준비되지 않았습니다.", status_code=409)
        return ProjectLayout.from_root(Path(str(project_path)))

    def _safe_workspace_read_path(self, layout: ProjectLayout, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise WebJobError("UNSAFE_PATH", "허용되지 않는 파일 경로입니다.", status_code=400)
        if candidate.parts and (candidate.parts[0] in {".appforge", ".git", ".hg", ".svn"}):
            raise WebJobError("MANAGED_PATH", "관리용 내부 경로는 워크스페이스 파일 API로 열 수 없습니다.", status_code=403)
        if any(part in IGNORED_DIRS for part in candidate.parts):
            raise WebJobError("IGNORED_PATH", "무시/캐시 경로는 웹에서 열 수 없습니다.", status_code=403)
        path = safe_resolve(layout.root, candidate)
        if not path.is_file() or path.is_symlink():
            raise WebJobError("FILE_NOT_FOUND", "파일을 찾을 수 없습니다.", status_code=404)
        return path

    def _run_job(self, job_id: str) -> None:
        layout: ProjectLayout | None = None
        cancel_event = self._cancel_events.setdefault(job_id, threading.Event())
        try:
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage="preflight")
                return
            self._set_job_running(job_id)
            self._set_stage(
                job_id,
                "preflight",
                "running",
                detail="외부 LLM 브릿지와 실행 환경을 확인하고 있습니다.",
            )
            readiness = self.driver_readiness()
            if not readiness["ready"]:
                error = self._make_error(
                    "AGENT_NOT_AVAILABLE",
                    readiness["message"],
                    action=readiness["action"],
                    stage="preflight",
                    technical={"driver": readiness},
                )
                self._fail_job(job_id, error, stage="preflight")
                return
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage="preflight")
                return
            try:
                selected_driver = create_driver(
                    str(readiness.get("selected") or self.config.driver),
                    unsafe=self.config.unsafe_agent,
                    model=self.config.model,
                    max_turns=self.config.max_turns,
                    bridge_url=self.config.llm_bridge_url,
                    llm_provider=self.config.llm_provider,
                )
            except DriverError as exc:
                error = self._make_error(
                    "DRIVER_ERROR",
                    str(exc),
                    action=readiness.get("action") or "에이전트 설치와 인증 상태를 확인하세요.",
                    stage="preflight",
                    technical={"driver": readiness},
                )
                self._fail_job(job_id, error, stage="preflight")
                return
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage="preflight")
                return
            self._update_job(
                job_id,
                driver=selected_driver.name,
                message=f"{readiness['label']} 확인을 완료했습니다.",
            )
            self._set_stage(
                job_id,
                "preflight",
                "completed",
                detail=f"{readiness['label']} 사용 준비 완료",
            )
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage="preflight")
                return

            self._set_stage(
                job_id,
                "project_setup",
                "running",
                detail="작업공간과 체크포인트를 만들고 있습니다.",
            )
            with self._lock:
                job = self._jobs[job_id]
                prompt = str(job["prompt"])
                pipeline_name = str(job["pipeline"])
                mode = str(job.get("mode") or "autonomous")
                resume_existing = bool(job.get("resume_existing"))
                project_path_value = str(job.get("project_path") or "")
                workspace_ref = str(job.get("workspace_ref") or "")
                only_stage = str(job.get("only_stage") or "") or None
                auto_approve = bool(job.get("auto_approve"))
            try:
                if resume_existing and project_path_value:
                    layout = ProjectLayout.from_root(Path(project_path_value))
                    if not (layout.control / PROJECT_FILE_NAME).is_file():
                        raise FileNotFoundError(f"AppForge project metadata missing at {layout.control}")
                elif workspace_ref:
                    source = Path(workspace_ref).expanduser().resolve()
                    revision_index = self._revision_index_for_job(job_id)
                    project_name = f"{source.name}-rev-{revision_index}-{job_id[:8]}"
                    target = (self.projects_dir / slugify(project_name)).resolve()
                    self._copy_workspace_for_revision(source, target)
                    layout = initialize_project(
                        prompt,
                        projects_dir=self.projects_dir,
                        name=target.name,
                        pipeline_name=pipeline_name,
                        mode=_project_mode(mode),
                        existing_target=target,
                    )
                else:
                    project_name = f"{slugify(prompt[:52])}-{job_id[:8]}"
                    layout = initialize_project(
                        prompt,
                        projects_dir=self.projects_dir,
                        name=project_name,
                        pipeline_name=pipeline_name,
                        mode=_project_mode(mode),
                    )
            except Exception as exc:
                error = self._make_error(
                    "PROJECT_INITIALIZATION_FAILED",
                    str(exc),
                    action="프로젝트 저장 경로의 쓰기 권한과 남은 디스크 공간을 확인하세요.",
                    stage="project_setup",
                    technical=self._exception_technical(exc),
                )
                self._fail_job(job_id, error, stage="project_setup")
                return
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage="project_setup")
                return
            self._update_job(
                job_id,
                project_name=layout.root.name,
                project_path=str(layout.root),
                status="running",
                message="앱 제작 파이프라인을 시작합니다.",
            )
            self._set_stage(
                job_id,
                "project_setup",
                "completed",
                detail=f"프로젝트 `{layout.root.name}` 준비 완료",
            )
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage="project_setup")
                return

            runner = PipelineRunner(
                layout,
                selected_driver,
                auto_approve=auto_approve,
                allow_network=self.config.allow_network,
                allow_destructive=self.config.allow_destructive,
                max_stage_attempts=self.config.max_stage_attempts,
                stage_timeout=self.config.stage_timeout,
                event_handler=lambda event: self._handle_runner_event(job_id, event),
                cancel_event=cancel_event,
            )
            summary = runner.run(only_stage=only_stage)
            if summary.status == "awaiting_human":
                self._await_approval_job(job_id, summary)
                return
            if not summary.success:
                if cancel_event.is_set() or (summary.failure or {}).get("code") == "JOB_CANCELLED":
                    self._cancel_job_state(job_id, stage=summary.failed_stage or summary.awaiting_stage)
                    return
                failure = summary.failure or {
                    "code": "STAGE_FAILED",
                    "message": summary.message,
                    "action": "오류 세부정보를 확인하고 같은 요청을 다시 실행하세요.",
                    "stage": summary.failed_stage or summary.awaiting_stage,
                }
                error = self._error_from_runner_failure(failure)
                self._fail_job(
                    job_id,
                    error,
                    stage=summary.failed_stage or summary.awaiting_stage,
                )
                return

            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage=summary.failed_stage or summary.awaiting_stage)
                return
            self._update_job(
                job_id,
                status="packaging",
                active_stage="download_package",
                message="완료된 소스를 다운로드 ZIP으로 확인하고 있습니다.",
            )
            self._set_stage(
                job_id,
                "download_package",
                "running",
                detail="비밀정보와 실행 캐시를 제외한 소스 ZIP을 준비하고 있습니다.",
            )
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage="download_package")
                return
            try:
                archive_path = self._ensure_archive(layout)
                self._validate_archive(archive_path)
            except WebJobError as exc:
                error = self._make_error(
                    exc.code,
                    exc.message,
                    action=exc.action,
                    stage="download_package",
                    technical=exc.context,
                )
                self._fail_job(job_id, error, stage="download_package")
                return
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage="download_package")
                return
            self._set_stage(
                job_id,
                "download_package",
                "completed",
                detail=f"{archive_path.name} 준비 완료",
            )
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "completed"
                job["message"] = "모든 단계가 완료되었습니다. ZIP 파일을 다운로드할 수 있습니다."
                job["active_stage"] = None
                job["completed_at"] = utc_now()
                job["updated_at"] = utc_now()
                job["archive_path"] = str(archive_path)
                job["download"] = {
                    "available": True,
                    "url": f"/api/jobs/{job_id}/download",
                    "filename": archive_path.name,
                    "size_bytes": archive_path.stat().st_size,
                }
                job["error"] = None
                self._record_event_locked(
                    job,
                    "job_completed",
                    "모든 단계가 완료되어 다운로드가 활성화되었습니다.",
                )
                self._save_locked(job)
        except Exception as exc:
            stage = self._current_stage(job_id)
            if cancel_event.is_set():
                self._cancel_job_state(job_id, stage=stage)
                return
            error = self._make_error(
                "UNEXPECTED_ERROR",
                f"{type(exc).__name__}: {exc}",
                action=(
                    "오류 세부정보와 서버 로그를 확인한 뒤 다시 실행하세요. 같은 지점에서 "
                    "반복되면 해당 단계의 도구 또는 에이전트 설정을 점검하세요."
                ),
                stage=stage,
                technical=self._exception_technical(exc),
            )
            self._fail_job(job_id, error, stage=stage)
        finally:
            with self._lock:
                self._threads.pop(job_id, None)
                job = self._jobs.get(job_id)
                if job is None or job.get("status") in TERMINAL_JOB_STATUSES | AWAITING_JOB_STATUSES:
                    self._cancel_events.pop(job_id, None)
                self._maybe_start_next_job_locked()

    def _await_approval_job(self, job_id: str, summary: Any) -> None:
        stage = summary.awaiting_stage or summary.failed_stage or self._current_stage(job_id)
        error = self._error_from_runner_failure(summary.failure or {
            "code": "HUMAN_APPROVAL_REQUIRED",
            "message": summary.message or "단계 승인이 필요합니다.",
            "action": "아티팩트를 검토하고 승인하거나 수정 요청을 입력하세요.",
            "stage": stage,
        })
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "awaiting_approval"
            job["message"] = "승인이 필요한 단계에서 대기 중입니다."
            job["active_stage"] = stage
            job["updated_at"] = utc_now()
            job["error"] = error
            self._record_event_locked(job, "job_awaiting_approval", "아티팩트 검토와 승인을 기다립니다.", data={"stage": stage})
            self._save_locked(job)

    def _revision_index_for_job(self, job_id: str) -> int:
        with self._lock:
            return int((self._jobs.get(job_id) or {}).get("revision_index") or 1)

    def _copy_workspace_for_revision(self, source: Path, target: Path) -> None:
        source = source.resolve()
        if not source.is_dir():
            raise FileNotFoundError(f"원본 작업공간을 찾을 수 없습니다: {source}")
        if target.exists():
            raise FileExistsError(f"수정 작업공간이 이미 존재합니다: {target}")

        def ignore(_dir: str, names: list[str]) -> set[str]:
            blocked = {".appforge", ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "dist", "build", "coverage", "__pycache__"}
            return {name for name in names if name in blocked or name.endswith(".pyc")}

        shutil.copytree(source, target, ignore=ignore, symlinks=False)

    def _ensure_archive(self, layout: ProjectLayout) -> Path:
        archives = sorted(
            layout.reports.glob("*-source.zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not archives:
            result = ArchiveWorkspaceTool().run(layout.root, {})
            if not result.success:
                raise WebJobError(
                    "ARCHIVE_CREATION_FAILED",
                    result.error or "소스 ZIP 생성 도구가 실패했습니다.",
                    action="쓰기 권한, 디스크 공간과 제외 규칙을 확인하세요.",
                    context={"tool_result": result.to_dict()},
                )
            archives = sorted(
                layout.reports.glob("*-source.zip"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        if not archives:
            raise WebJobError(
                "ARCHIVE_CREATION_FAILED",
                "소스 ZIP 생성 단계가 성공으로 끝났지만 파일이 만들어지지 않았습니다.",
                action=".appforge/reports 경로의 쓰기 권한과 서버 로그를 확인하세요.",
            )
        archive = archives[0].resolve()
        reports = layout.reports.resolve()
        if reports not in archive.parents:
            raise WebJobError(
                "ARCHIVE_CREATION_FAILED",
                "생성된 ZIP 경로가 허용된 보고서 폴더 밖에 있습니다.",
                action="아카이브 도구 설정을 기본값으로 되돌리세요.",
                context={"archive": str(archive), "reports": str(reports)},
            )
        return archive

    def _validate_archive(self, archive: Path) -> None:
        if not archive.is_file() or not zipfile.is_zipfile(archive):
            raise WebJobError(
                "ARCHIVE_INVALID",
                "생성된 파일이 유효한 ZIP 형식이 아닙니다.",
                action="디스크 공간을 확인한 뒤 다시 실행하세요.",
                context={"archive": str(archive)},
            )
        try:
            with zipfile.ZipFile(archive) as handle:
                damaged = handle.testzip()
                if damaged:
                    raise WebJobError(
                        "ARCHIVE_INVALID",
                        f"ZIP 내부 파일이 손상되었습니다: {damaged}",
                        action="손상된 산출물을 삭제하고 다시 실행하세요.",
                        context={"archive": str(archive), "damaged_entry": damaged},
                    )
        except zipfile.BadZipFile as exc:
            raise WebJobError(
                "ARCHIVE_INVALID",
                str(exc),
                action="다운로드 패키지를 다시 생성하세요.",
                context={"archive": str(archive)},
            ) from exc

    def _handle_runner_event(self, job_id: str, event: dict[str, Any]) -> None:
        event_name = str(event.get("event") or "")
        stage = event.get("stage")
        attempt = _safe_int(event.get("attempt"))
        if event_name == "stage_started" and stage:
            self._update_job(
                job_id,
                status="running",
                active_stage=str(stage),
                message=f"{self._stage_title(str(stage))} 단계를 시작했습니다.",
            )
            self._set_stage(
                job_id,
                str(stage),
                "running",
                detail="단계 작업을 준비하고 있습니다.",
                attempt=attempt,
            )
        elif event_name == "attempt_started" and stage:
            max_attempts = _safe_int(event.get("max_attempts"))
            detail = f"{attempt or 1}번째 시도를 시작했습니다."
            if max_attempts:
                detail = f"{attempt or 1}/{max_attempts}번째 시도를 시작했습니다."
            self._set_stage(
                job_id,
                str(stage),
                "running",
                detail=detail,
                attempt=attempt,
            )
        elif event_name == "agent_started" and stage:
            self._set_stage(
                job_id,
                str(stage),
                "running",
                detail="외부 LLM이 소스와 단계 산출물을 만들고 있습니다.",
                attempt=attempt,
            )
        elif event_name == "agent_completed" and stage:
            success = bool(event.get("success"))
            detail = (
                "에이전트 작업을 마쳐 결과를 검증합니다."
                if success
                else "에이전트 실행 오류를 분석하고 있습니다."
            )
            self._set_stage(
                job_id,
                str(stage),
                "validating",
                detail=detail,
                attempt=attempt,
            )
        elif event_name == "validation_started" and stage:
            self._set_stage(
                job_id,
                str(stage),
                "validating",
                detail="산출물, 테스트, 빌드와 필수 게이트를 검증하고 있습니다.",
                attempt=attempt,
            )
        elif event_name == "llm_text" and stage:
            delta = _clean_text(str(event.get("delta") or ""), 500)
            if delta:
                self._set_stage(
                    job_id,
                    str(stage),
                    "running",
                    detail=f"LLM 응답 수신 중: {delta[-160:]}",
                    attempt=attempt,
                )
        elif event_name == "tool_call" and stage:
            name = str(event.get("name") or "tool")
            self._set_stage(
                job_id,
                str(stage),
                "running",
                detail=f"도구 실행 중: {name}",
                attempt=attempt,
            )
        elif event_name == "attempt_failed" and stage:
            failure = self._error_from_runner_failure(event.get("failure") or {})
            self._set_stage(
                job_id,
                str(stage),
                "retrying",
                detail=failure["message"],
                attempt=attempt,
                error=self._compact_error(failure),
            )
        elif event_name == "stage_retrying" and stage:
            next_attempt = _safe_int(event.get("next_attempt"))
            failure = self._error_from_runner_failure(event.get("failure") or {})
            self._update_job(
                job_id,
                message=(
                    f"{self._stage_title(str(stage))} 검증이 실패해 "
                    f"{next_attempt or '다음'}번째 시도를 준비합니다."
                ),
            )
            self._set_stage(
                job_id,
                str(stage),
                "retrying",
                detail=f"자동 수정 후 {next_attempt or '다음'}번째 시도를 진행합니다.",
                attempt=attempt,
                error=self._compact_error(failure),
            )
        elif event_name == "stage_completed" and stage:
            self._set_stage(
                job_id,
                str(stage),
                "completed",
                detail="필수 산출물과 검증을 통과했습니다.",
                attempt=attempt,
                error=None,
            )
            self._update_job(
                job_id,
                message=f"{self._stage_title(str(stage))} 단계를 완료했습니다.",
            )
        elif event_name == "stage_awaiting_approval" and stage:
            failure = self._error_from_runner_failure(event.get("failure") or {})
            self._set_stage(
                job_id,
                str(stage),
                "awaiting_approval",
                detail=failure["message"],
                attempt=attempt,
                error=self._compact_error(failure),
            )
            self._update_job(
                job_id,
                status="awaiting_approval",
                active_stage=str(stage),
                message="중간 산출물 검토와 승인이 필요합니다.",
            )
        elif event_name == "stage_failed" and stage:
            failure = self._error_from_runner_failure(event.get("failure") or {})
            self._set_stage(
                job_id,
                str(stage),
                "failed",
                detail=failure["message"],
                attempt=attempt,
                error=self._compact_error(failure),
            )

        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                compact = {
                    "event": event_name,
                    "stage": stage,
                    "attempt": attempt,
                    "timestamp": event.get("timestamp") or utc_now(),
                }
                self._record_event_locked(
                    job,
                    event_name,
                    self._event_message(event_name, stage, attempt),
                    data=compact,
                )
                self._save_locked(job)

    def _initial_stages(self, pipeline: PipelineSpec) -> list[dict[str, Any]]:
        stages = [
            {
                "id": "preflight",
                "name": "실행 환경 확인",
                "description": "외부 LLM 브릿지, 인증과 기본 안전 설정을 확인합니다.",
                "kind": "system",
                "status": "pending",
                "detail": "대기 중",
                "attempt": 0,
                "started_at": None,
                "completed_at": None,
                "error": None,
            },
            {
                "id": "project_setup",
                "name": "프로젝트 준비",
                "description": "자동 파이프라인과 안전한 작업공간을 준비합니다.",
                "kind": "system",
                "status": "pending",
                "detail": f"자동 선택: {pipeline.name}",
                "attempt": 0,
                "started_at": None,
                "completed_at": None,
                "error": None,
            },
        ]
        for spec in pipeline.stages:
            stages.append(self._stage_record(spec))
        stages.append(
            {
                "id": "download_package",
                "name": "다운로드 준비",
                "description": "완료된 소스를 검사하고 ZIP 다운로드를 활성화합니다.",
                "kind": "system",
                "status": "pending",
                "detail": "대기 중",
                "attempt": 0,
                "started_at": None,
                "completed_at": None,
                "error": None,
            }
        )
        return stages

    def _stage_record(self, spec: StageSpec) -> dict[str, Any]:
        title, description = STAGE_COPY.get(
            spec.name,
            (spec.name.replace("_", " ").title(), spec.description),
        )
        return {
            "id": spec.name,
            "name": title,
            "description": description,
            "kind": "pipeline",
            "status": "pending",
            "detail": "대기 중",
            "attempt": 0,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "produces": list(spec.produces),
            "approval": bool(spec.approval),
        }

    def _set_job_running(self, job_id: str) -> None:
        with self._lock:
            job = self._require_job_locked(job_id)
            now = utc_now()
            job["status"] = "initializing"
            job["message"] = "실행 환경을 확인하고 있습니다."
            job["started_at"] = job.get("started_at") or now
            job["updated_at"] = now
            job["active_stage"] = "preflight"
            self._record_event_locked(job, "job_started", "작업 실행을 시작했습니다.")
            self._save_locked(job)

    def _set_stage(
        self,
        job_id: str,
        stage_id: str,
        status: str,
        *,
        detail: str,
        attempt: int | None = None,
        error: dict[str, Any] | None | object = _UNSET,
    ) -> None:
        with self._lock:
            job = self._require_job_locked(job_id)
            stage = self._find_stage_locked(job, stage_id)
            if stage is None:
                return
            now = utc_now()
            stage["status"] = status
            stage["detail"] = detail
            if attempt is not None:
                stage["attempt"] = attempt
            if status in {"running", "validating", "retrying", "awaiting_approval"}:
                stage["started_at"] = stage.get("started_at") or now
                job["active_stage"] = stage_id
            if status == "completed":
                stage["started_at"] = stage.get("started_at") or now
                stage["completed_at"] = now
            if status == "failed":
                stage["completed_at"] = now
            if error is not _UNSET:
                stage["error"] = error
            job["updated_at"] = now
            self._save_locked(job)

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._require_job_locked(job_id)
            job.update(updates)
            job["updated_at"] = utc_now()
            self._save_locked(job)

    def _fail_job(
        self,
        job_id: str,
        error: dict[str, Any],
        *,
        stage: str | None,
    ) -> None:
        with self._lock:
            job = self._require_job_locked(job_id)
            now = utc_now()
            if stage:
                stage_record = self._find_stage_locked(job, stage)
                if stage_record is not None:
                    stage_record["status"] = "failed"
                    stage_record["detail"] = error["message"]
                    stage_record["error"] = self._compact_error(error)
                    stage_record["started_at"] = stage_record.get("started_at") or now
                    stage_record["completed_at"] = now
            job["status"] = "failed"
            job["message"] = error["message"]
            job["active_stage"] = stage
            job["error"] = error
            job["completed_at"] = now
            job["updated_at"] = now
            job["download"] = {
                "available": False,
                "url": None,
                "filename": None,
                "size_bytes": None,
            }
            self._record_event_locked(
                job,
                "job_failed",
                error["message"],
                data={"code": error.get("code"), "stage": stage},
            )
            self._save_locked(job)

    def _cancel_job_state(self, job_id: str, *, stage: str | None) -> None:
        error = self._make_error(
            "JOB_CANCELLED",
            "사용자가 실행 중인 작업을 취소했습니다.",
            action="필요하면 새 요청을 시작하세요.",
            stage=stage,
        )
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._mark_cancelled_locked(job, error, stage=stage)

    def _mark_cancelled_locked(
        self,
        job: dict[str, Any],
        error: dict[str, Any],
        *,
        stage: str | None,
    ) -> None:
        now = utc_now()
        if stage:
            stage_record = self._find_stage_locked(job, stage)
            if stage_record is not None and stage_record.get("status") not in {"completed", "failed"}:
                stage_record["status"] = "failed"
                stage_record["detail"] = error["message"]
                stage_record["error"] = self._compact_error(error)
                stage_record["started_at"] = stage_record.get("started_at") or now
                stage_record["completed_at"] = now
        job["status"] = "cancelled"
        job["message"] = error["message"]
        job["active_stage"] = stage
        job["error"] = error
        job["completed_at"] = now
        job["updated_at"] = now
        job["download"] = {
            "available": False,
            "url": None,
            "filename": None,
            "size_bytes": None,
        }
        if not any(event.get("event") == "job_cancelled" for event in job.get("events") or []):
            self._record_event_locked(
                job,
                "job_cancelled",
                error["message"],
                data={"code": error.get("code"), "stage": stage},
            )
        self._save_locked(job)

    def _public_job_locked(self, job: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(job)
        public.pop("archive_path", None)
        public["progress"] = self._progress(job)
        public["terminal"] = job.get("status") in TERMINAL_JOB_STATUSES
        public["queue_position"] = self._queue_position_locked(str(job.get("id") or ""))
        public["status_label"] = {
            "queued": "실행 대기",
            "initializing": "환경 확인 중",
            "running": "앱 생성 중",
            "packaging": "ZIP 준비 중",
            "awaiting_approval": "승인 대기",
            "completed": "완료",
            "failed": "오류 발생",
            "cancelled": "취소됨",
        }.get(str(job.get("status")), str(job.get("status")))
        return public

    def _progress(self, job: dict[str, Any]) -> int:
        stages = job.get("stages") or []
        if not stages:
            return 0
        if job.get("status") == "completed":
            return 100
        weights = {
            "pending": 0.0,
            "running": 0.35,
            "validating": 0.7,
            "retrying": 0.55,
            "completed": 1.0,
            "failed": 0.65,
            "awaiting_approval": 0.75,
        }
        points = sum(weights.get(str(stage.get("status")), 0.0) for stage in stages)
        return min(99, max(0, round((points / len(stages)) * 100)))

    def _make_error(
        self,
        code: str,
        message: str,
        *,
        action: str,
        stage: str | None,
        attempt: int | None = None,
        technical: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "title": ERROR_TITLES.get(code, "작업을 완료하지 못했습니다"),
            "message": _clean_text(message, 6_000),
            "action": _clean_text(action, 4_000),
            "stage": stage,
            "attempt": attempt,
            "technical": _sanitize_value(technical or {}, max_text=12_000),
        }

    def _error_from_runner_failure(self, failure: dict[str, Any]) -> dict[str, Any]:
        code = str(failure.get("code") or "STAGE_FAILED")
        technical = {
            key: value
            for key, value in failure.items()
            if key not in {"code", "message", "action", "stage", "attempt"}
        }
        return self._make_error(
            code,
            str(failure.get("message") or "단계 실행이 실패했습니다."),
            action=str(
                failure.get("action")
                or "오류 세부정보를 확인하고 같은 요청을 다시 실행하세요."
            ),
            stage=str(failure.get("stage")) if failure.get("stage") else None,
            attempt=_safe_int(failure.get("attempt")),
            technical=technical,
        )

    def _compact_error(self, error: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": error.get("code"),
            "title": error.get("title"),
            "message": _clean_text(str(error.get("message") or ""), 1_000),
            "action": _clean_text(str(error.get("action") or ""), 1_000),
        }

    def _exception_technical(self, exc: Exception) -> dict[str, Any]:
        return {
            "exception_type": type(exc).__name__,
            "exception": _clean_text(str(exc), 4_000),
            "traceback": _clean_text(traceback.format_exc(limit=16), 16_000),
        }

    def _current_stage(self, job_id: str) -> str | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return str(job.get("active_stage")) if job and job.get("active_stage") else None

    def _stage_title(self, stage_id: str) -> str:
        return STAGE_COPY.get(stage_id, (stage_id.replace("_", " ").title(), ""))[0]

    def _event_message(
        self,
        event_name: str,
        stage: Any,
        attempt: int | None,
    ) -> str:
        title = self._stage_title(str(stage)) if stage else "파이프라인"
        suffix = f" ({attempt}번째 시도)" if attempt else ""
        messages = {
            "stage_started": f"{title} 시작",
            "attempt_started": f"{title} 시도 시작{suffix}",
            "agent_started": f"{title} 에이전트 실행{suffix}",
            "agent_completed": f"{title} 에이전트 실행 종료{suffix}",
            "validation_started": f"{title} 검증 시작{suffix}",
            "llm_text": f"{title} LLM 스트림",
            "tool_call": f"{title} 도구 실행",
            "preview_built": "프리뷰 준비",
            "attempt_failed": f"{title} 시도 실패{suffix}",
            "stage_retrying": f"{title} 자동 재시도 준비",
            "stage_completed": f"{title} 완료",
            "stage_failed": f"{title} 실패",
            "stage_awaiting_approval": f"{title} 승인 대기",
            "job_awaiting_approval": "사용자 승인 대기",
            "job_approved": "사용자 승인 완료",
            "stage_retry_requested": f"{title} 재시도 요청",
            "revision_queued": "수정 작업 등록",
            "job_dequeued": "대기열 실행 시작",
            "loop_guard_triggered": f"{title} 반복 실패 루프 감지",
            "pipeline_completed": "파이프라인 완료",
            "pipeline_failed": "파이프라인 실패",
        }
        return messages.get(event_name, event_name)

    def _ensure_queue_capacity_locked(self) -> None:
        waiting = [
            job
            for job in self._jobs.values()
            if job.get("status") in (QUEUED_JOB_STATUSES | RUNNING_JOB_STATUSES)
        ]
        if len(waiting) >= self.config.queue_limit:
            raise WebJobError(
                "QUEUE_FULL",
                "실행 대기열이 가득 찼습니다.",
                action="현재 작업 몇 개가 완료된 뒤 다시 요청하세요.",
                status_code=409,
                context={"queue_limit": self.config.queue_limit},
            )

    def _running_job_id_locked(self) -> str | None:
        active = [job for job in self._jobs.values() if job.get("status") in RUNNING_JOB_STATUSES]
        if not active:
            return None
        active.sort(key=lambda item: str(item.get("started_at") or item.get("updated_at") or ""), reverse=True)
        return str(active[0]["id"])

    def _maybe_start_next_job_locked(self) -> None:
        if self._running_job_id_locked() is not None:
            return
        queued = [job for job in self._jobs.values() if job.get("status") == "queued"]
        if not queued:
            return
        queued.sort(key=lambda item: str(item.get("created_at") or ""))
        job = queued[0]
        job_id = str(job["id"])
        if job_id in self._threads:
            return
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id,),
            name=f"appforge-job-{job_id[:8]}",
            daemon=True,
        )
        self._threads[job_id] = thread
        self._cancel_events.setdefault(job_id, threading.Event())
        self._record_event_locked(job, "job_dequeued", "작업이 대기열에서 실행을 시작합니다.")
        self._save_locked(job)
        thread.start()

    def _queue_position_locked(self, job_id: str) -> int | None:
        queued = [job for job in self._jobs.values() if job.get("status") == "queued"]
        queued.sort(key=lambda item: str(item.get("created_at") or ""))
        for index, job in enumerate(queued, start=1):
            if str(job.get("id")) == job_id:
                return index
        return None

    def _active_job_id_locked(self) -> str | None:
        running = self._running_job_id_locked()
        if running is not None:
            return running
        awaiting = [job for job in self._jobs.values() if job.get("status") in AWAITING_JOB_STATUSES]
        if awaiting:
            awaiting.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
            return str(awaiting[0]["id"])
        queued = [job for job in self._jobs.values() if job.get("status") == "queued"]
        if queued:
            queued.sort(key=lambda item: str(item.get("created_at") or ""))
            return str(queued[0]["id"])
        return None

    def _find_stage_locked(
        self,
        job: dict[str, Any],
        stage_id: str,
    ) -> dict[str, Any] | None:
        for stage in job.get("stages") or []:
            if stage.get("id") == stage_id:
                return stage
        return None

    def _require_job_locked(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise WebJobError("JOB_NOT_FOUND", "작업을 찾을 수 없습니다.", status_code=404)
        return job

    def _record_event_locked(
        self,
        job: dict[str, Any],
        event: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        events = job.setdefault("events", [])
        record = {
            "event": event,
            "message": _clean_text(message, 1_000),
            "timestamp": utc_now(),
            "data": _sanitize_value(data or {}, max_text=1_000),
        }
        events.append(record)
        if len(events) > MAX_EVENTS:
            del events[: len(events) - MAX_EVENTS]
        for subscriber in list(self._event_subscribers.get(str(job.get("id")), [])):
            try:
                subscriber.put_nowait(record)
            except queue.Full:
                pass

    def _save_locked(self, job: dict[str, Any]) -> None:
        atomic_write_json(self.jobs_dir / f"{job['id']}.json", job)

    def _load_jobs(self) -> None:
        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                data = read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or not data.get("id"):
                continue
            job_id = str(data["id"])
            if data.get("status") in RUNNING_JOB_STATUSES:
                now = utc_now()
                stage_id = str(data.get("active_stage") or "preflight")
                stage = self._find_stage_locked(data, stage_id)
                error = self._make_error(
                    "SERVER_RESTARTED",
                    "작업이 실행 중일 때 웹 서버가 종료되거나 다시 시작되었습니다.",
                    action="같은 요청을 다시 실행하세요. 기존 작업공간은 프로젝트 폴더에 보존됩니다.",
                    stage=stage_id,
                    technical={"previous_status": data.get("status")},
                )
                if stage is not None:
                    stage["status"] = "failed"
                    stage["detail"] = error["message"]
                    stage["error"] = self._compact_error(error)
                    stage["completed_at"] = now
                data["status"] = "failed"
                data["message"] = error["message"]
                data["error"] = error
                data["completed_at"] = now
                data["updated_at"] = now
                data["download"] = {
                    "available": False,
                    "url": None,
                    "filename": None,
                    "size_bytes": None,
                }
                atomic_write_json(path, data)
            elif data.get("status") == "queued":
                # Queued jobs survive process restarts and will be relaunched after
                # _load_jobs() finishes via _maybe_start_next_job_locked().
                data["updated_at"] = utc_now()
                atomic_write_json(path, data)
            elif data.get("status") in AWAITING_JOB_STATUSES:
                # Approval waits are durable user decisions, not crashed executions.
                data["message"] = data.get("message") or "승인이 필요한 단계에서 대기 중입니다."
                data["updated_at"] = utc_now()
                atomic_write_json(path, data)
            elif data.get("status") == "completed":
                archive_value = data.get("archive_path")
                if not archive_value or not Path(str(archive_value)).is_file():
                    error = self._make_error(
                        "ARCHIVE_MISSING",
                        "완료된 작업의 ZIP 파일이 이동되었거나 삭제되었습니다.",
                        action="같은 요청을 다시 실행해 다운로드 패키지를 재생성하세요.",
                        stage="download_package",
                        technical={"archive_path": archive_value},
                    )
                    data["status"] = "failed"
                    data["message"] = error["message"]
                    data["error"] = error
                    data["download"] = {
                        "available": False,
                        "url": None,
                        "filename": None,
                        "size_bytes": None,
                    }
                    data["updated_at"] = utc_now()
                    atomic_write_json(path, data)
            self._jobs[job_id] = data


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_job_mode(mode: str | None) -> str:
    normalized = (mode or "autonomous").strip().casefold().replace("_", "-")
    if normalized in {"guided", "checkpoint", "checkpoints"}:
        return "checkpoint"
    if normalized in {"auto", "autonomous", "autonomy"}:
        return "autonomous"
    raise WebJobError(
        "INVALID_MODE",
        "실행 모드는 autonomous 또는 checkpoint 중 하나여야 합니다.",
        status_code=422,
    )


def _project_mode(job_mode: str) -> str:
    return "guided" if job_mode == "checkpoint" else "autonomous"


def _artifact_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "JSON 아티팩트"
    for key in ("title", "name", "summary", "objective", "goal"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value, 160)
    if payload.get("requirements") and isinstance(payload["requirements"], list):
        return f"requirements {len(payload['requirements'])}개"
    if payload.get("checks") and isinstance(payload["checks"], list):
        return f"checks {len(payload['checks'])}개"
    return ", ".join(list(payload.keys())[:5]) or "JSON 아티팩트"


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime_from_timestamp(path.stat().st_mtime)
    except OSError:
        return None


def datetime_from_timestamp(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")



def _parse_json_dict(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text.strip())
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(text[start : end + 1])
        if isinstance(value, dict):
            return value
    raise ValueError("Router response did not contain a JSON object")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed

def _clean_text(value: str, limit: int) -> str:
    return redact(truncate(value, limit))


def _sanitize_value(value: Any, *, max_text: int) -> Any:
    if isinstance(value, str):
        return _clean_text(value, max_text)
    if isinstance(value, dict):
        return {
            str(key): _sanitize_value(item, max_text=max_text)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, max_text=max_text) for item in value[:200]]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _clean_text(str(value), max_text)
