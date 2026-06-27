from __future__ import annotations

import copy
import json
import os
import shlex
import threading
import traceback
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .drivers import DriverError, create_driver
from .models import PipelineSpec, ProjectLayout, StageSpec
from .pipelines import auto_select_pipeline, load_pipeline
from .projects import initialize_project
from .runner import PipelineRunner
from .tooling.tools.release import ArchiveWorkspaceTool
from .util import (
    atomic_write_json,
    command_exists,
    read_json,
    redact,
    slugify,
    truncate,
    utc_now,
)

ACTIVE_JOB_STATUSES = {"queued", "initializing", "running", "packaging"}
TERMINAL_JOB_STATUSES = {"completed", "failed"}
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
    "AGENT_NOT_AVAILABLE": "코딩 에이전트를 찾을 수 없습니다",
    "DRIVER_ERROR": "코딩 에이전트를 시작하지 못했습니다",
    "AGENT_PROCESS_FAILED": "코딩 에이전트 실행이 실패했습니다",
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
LLM_BRIDGE_DRIVER_ALIASES = {"llm-bridge", "llm_bridge", "llm"}


@dataclass(frozen=True)
class WebConfig:
    projects_dir: Path = field(default_factory=lambda: Path("projects"))
    data_dir: Path = field(default_factory=lambda: Path(".appforge-web"))
    driver: str = "auto"
    agent_cmd: str | None = None
    model: str | None = None
    allow_network: bool = True
    allow_destructive: bool = False
    unsafe_agent: bool = False
    max_stage_attempts: int | None = None
    stage_timeout: int = 3600
    max_turns: int | None = None
    prompt_max_chars: int = 20_000
    llm_bridge_url: str = DEFAULT_LLM_BRIDGE_URL
    llm_provider: str | None = None

    @classmethod
    def from_env(cls) -> "WebConfig":
        return cls(
            projects_dir=Path(os.environ.get("APPFORGE_PROJECTS_DIR", "projects")),
            data_dir=Path(os.environ.get("APPFORGE_DATA_DIR", ".appforge-web")),
            driver=os.environ.get("APPFORGE_DRIVER", "auto"),
            agent_cmd=os.environ.get("APPFORGE_AGENT_CMD") or None,
            model=os.environ.get("APPFORGE_MODEL") or None,
            allow_network=_env_bool("APPFORGE_ALLOW_NETWORK", True),
            allow_destructive=_env_bool("APPFORGE_ALLOW_DESTRUCTIVE", False),
            unsafe_agent=_env_bool("APPFORGE_UNSAFE_AGENT", False),
            max_stage_attempts=_env_optional_int("APPFORGE_MAX_STAGE_ATTEMPTS"),
            stage_timeout=_env_int("APPFORGE_STAGE_TIMEOUT", 3600, minimum=60),
            max_turns=_env_optional_int("APPFORGE_MAX_TURNS"),
            prompt_max_chars=_env_int("APPFORGE_PROMPT_MAX_CHARS", 20_000, minimum=100),
            llm_bridge_url=os.environ.get("APPFORGE_LLM_BRIDGE_URL", DEFAULT_LLM_BRIDGE_URL),
            llm_provider=os.environ.get("APPFORGE_LLM_PROVIDER") or None,
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
        self._load_jobs()

    def health(self) -> dict[str, Any]:
        readiness = self.driver_readiness()
        with self._lock:
            active_job_id = self._active_job_id_locked()
        return {
            "status": "ready" if readiness["ready"] else "needs_setup",
            "ready": readiness["ready"],
            "version": __version__,
            "driver": readiness,
            "busy": active_job_id is not None,
            "active_job_id": active_job_id,
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
            if command_exists("codex"):
                return {
                    "ready": True,
                    "requested": "auto",
                    "selected": "codex",
                    "label": "Codex CLI",
                    "message": "Codex CLI를 사용합니다.",
                    "action": "",
                }
            if command_exists("claude"):
                return {
                    "ready": True,
                    "requested": "auto",
                    "selected": "claude",
                    "label": "Claude Code CLI",
                    "message": "Claude Code CLI를 사용합니다.",
                    "action": "",
                }
            return {
                "ready": False,
                "requested": "auto",
                "selected": None,
                "label": "코딩 에이전트 없음",
                "message": "Codex CLI 또는 Claude Code CLI가 PATH에서 발견되지 않았습니다.",
                "action": (
                    "Codex CLI 또는 Claude Code CLI를 설치하고 로그인한 뒤 웹앱을 다시 "
                    "시작하세요. 사용자 명령을 쓰려면 APPFORGE_DRIVER=generic과 "
                    "APPFORGE_AGENT_CMD를 설정할 수 있습니다."
                ),
            }
        if requested == "codex":
            ready = command_exists("codex")
            return {
                "ready": ready,
                "requested": requested,
                "selected": "codex" if ready else None,
                "label": "Codex CLI",
                "message": "Codex CLI를 사용합니다." if ready else "Codex CLI를 찾지 못했습니다.",
                "action": "Codex CLI를 설치하고 로그인한 뒤 서버를 다시 시작하세요." if not ready else "",
            }
        if requested == "claude":
            ready = command_exists("claude")
            return {
                "ready": ready,
                "requested": requested,
                "selected": "claude" if ready else None,
                "label": "Claude Code CLI",
                "message": (
                    "Claude Code CLI를 사용합니다."
                    if ready
                    else "Claude Code CLI를 찾지 못했습니다."
                ),
                "action": (
                    "Claude Code CLI를 설치하고 로그인한 뒤 서버를 다시 시작하세요."
                    if not ready
                    else ""
                ),
            }
        if requested == "generic":
            if not self.config.agent_cmd:
                return {
                    "ready": False,
                    "requested": requested,
                    "selected": None,
                    "label": "사용자 명령",
                    "message": "APPFORGE_AGENT_CMD가 설정되지 않았습니다.",
                    "action": "실행할 에이전트 명령 템플릿을 APPFORGE_AGENT_CMD에 설정하세요.",
                }
            executable = _command_executable(self.config.agent_cmd)
            ready = executable is not None and _executable_exists(executable)
            return {
                "ready": ready,
                "requested": requested,
                "selected": "generic" if ready else None,
                "label": "사용자 명령",
                "message": (
                    f"사용자 명령 실행기 `{executable}`를 사용합니다."
                    if ready
                    else f"사용자 명령의 실행 파일 `{executable or '?'}`을 찾지 못했습니다."
                ),
                "action": (
                    "APPFORGE_AGENT_CMD의 첫 실행 파일과 PATH를 확인하세요."
                    if not ready
                    else ""
                ),
            }
        return {
            "ready": False,
            "requested": requested,
            "selected": None,
            "label": "알 수 없는 실행기",
            "message": f"지원하지 않는 드라이버입니다: {self.config.driver}",
            "action": "APPFORGE_DRIVER를 auto, codex, claude, generic 또는 llm-bridge로 설정하세요.",
        }

    def _llm_bridge_readiness(self) -> dict[str, Any]:
        from . import llm_bridge

        bridge_url = self.config.llm_bridge_url
        try:
            llm_bridge.ping(bridge_url)
            active = llm_bridge.get_active(bridge_url)
            provider_payload = llm_bridge.list_providers(bridge_url)
        except llm_bridge.BridgeError as exc:
            return {
                "ready": False,
                "requested": "llm-bridge",
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
                "requested": "llm-bridge",
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
                "requested": "llm-bridge",
                "selected": None,
                "label": "LLM 브릿지",
                "message": f"알 수 없는 프로바이더입니다: {provider}",
                "action": "설정 패널에서 지원되는 프로바이더를 선택하세요.",
            }
        if not provider_status.get("configured"):
            return {
                "ready": False,
                "requested": "llm-bridge",
                "selected": None,
                "label": "LLM 브릿지",
                "message": f"{provider} 프로바이더 설정이 완료되지 않았습니다.",
                "action": "설정 패널에서 API 키와 필요한 Base URL을 저장하세요.",
            }
        return {
            "ready": True,
            "requested": "llm-bridge",
            "selected": "llm-bridge",
            "label": f"LLM 브릿지 · {provider}",
            "message": f"{provider}/{model or '기본 모델'} 을(를) 사용합니다.",
            "action": "",
        }

    def create_job(self, prompt: str) -> dict[str, Any]:
        normalized = prompt.strip()
        if not normalized:
            raise WebJobError(
                "EMPTY_PROMPT",
                "만들 앱의 목적과 핵심 기능을 입력하세요.",
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

        selected, _scores = auto_select_pipeline(normalized, existing_repo=False)
        pipeline = load_pipeline(selected)
        job_id = uuid.uuid4().hex
        now = utc_now()
        job = {
            "id": job_id,
            "version": "1.0",
            "prompt": normalized,
            "status": "queued",
            "message": "실행을 준비하고 있습니다.",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "pipeline": pipeline.name,
            "pipeline_description": pipeline.description,
            "driver": None,
            "project_name": None,
            "project_path": None,
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
        }
        with self._lock:
            active_job_id = self._active_job_id_locked()
            if active_job_id is not None:
                raise WebJobError(
                    "JOB_ALREADY_RUNNING",
                    "현재 다른 앱을 만들고 있습니다.",
                    action="현재 작업 상태를 이어서 확인한 뒤 완료되면 새 요청을 시작하세요.",
                    status_code=409,
                    context={"current_job_id": active_job_id},
                )
            self._jobs[job_id] = job
            self._record_event_locked(job, "job_queued", "작업이 실행 대기열에 등록되었습니다.")
            self._save_locked(job)
            thread = threading.Thread(
                target=self._run_job,
                args=(job_id,),
                name=f"appforge-job-{job_id[:8]}",
                daemon=True,
            )
            self._threads[job_id] = thread
            thread.start()
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
        # Worker threads are daemon threads so a local web server can stop immediately.
        return

    def _run_job(self, job_id: str) -> None:
        layout: ProjectLayout | None = None
        try:
            self._set_job_running(job_id)
            self._set_stage(
                job_id,
                "preflight",
                "running",
                detail="설치된 코딩 에이전트와 실행 환경을 확인하고 있습니다.",
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
            try:
                selected_driver = create_driver(
                    self.config.driver,
                    unsafe=self.config.unsafe_agent,
                    model=self.config.model,
                    agent_cmd=self.config.agent_cmd,
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
            project_name = f"{slugify(prompt[:52])}-{job_id[:8]}"
            try:
                layout = initialize_project(
                    prompt,
                    projects_dir=self.projects_dir,
                    name=project_name,
                    pipeline_name=pipeline_name,
                    mode="autonomous",
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

            runner = PipelineRunner(
                layout,
                selected_driver,
                auto_approve=True,
                allow_network=self.config.allow_network,
                allow_destructive=self.config.allow_destructive,
                max_stage_attempts=self.config.max_stage_attempts,
                stage_timeout=self.config.stage_timeout,
                event_handler=lambda event: self._handle_runner_event(job_id, event),
            )
            summary = runner.run()
            if not summary.success:
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
                detail="코딩 에이전트가 소스와 단계 산출물을 만들고 있습니다.",
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
        elif event_name in {"stage_failed", "stage_awaiting_approval"} and stage:
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
                "description": "코딩 에이전트 설치, 인증과 기본 안전 설정을 확인합니다.",
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
            if status in {"running", "validating", "retrying"}:
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

    def _public_job_locked(self, job: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(job)
        public.pop("archive_path", None)
        public["progress"] = self._progress(job)
        public["terminal"] = job.get("status") in TERMINAL_JOB_STATUSES
        public["status_label"] = {
            "queued": "실행 대기",
            "initializing": "환경 확인 중",
            "running": "앱 생성 중",
            "packaging": "ZIP 준비 중",
            "completed": "완료",
            "failed": "오류 발생",
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
            "attempt_failed": f"{title} 시도 실패{suffix}",
            "stage_retrying": f"{title} 자동 재시도 준비",
            "stage_completed": f"{title} 완료",
            "stage_failed": f"{title} 실패",
            "loop_guard_triggered": f"{title} 반복 실패 루프 감지",
            "pipeline_completed": "파이프라인 완료",
            "pipeline_failed": "파이프라인 실패",
        }
        return messages.get(event_name, event_name)

    def _active_job_id_locked(self) -> str | None:
        active = [
            job
            for job in self._jobs.values()
            if job.get("status") in ACTIVE_JOB_STATUSES
        ]
        if not active:
            return None
        active.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return str(active[0]["id"])

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
        events.append(
            {
                "event": event,
                "message": _clean_text(message, 1_000),
                "timestamp": utc_now(),
                "data": _sanitize_value(data or {}, max_text=1_000),
            }
        )
        if len(events) > MAX_EVENTS:
            del events[: len(events) - MAX_EVENTS]

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
            if data.get("status") in ACTIVE_JOB_STATUSES:
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


def _command_executable(command_template: str) -> str | None:
    try:
        tokens = shlex.split(command_template, posix=os.name != "nt")
    except ValueError:
        return None
    return tokens[0] if tokens else None


def _executable_exists(executable: str) -> bool:
    path = Path(executable).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        return path.is_file()
    return command_exists(executable)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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
