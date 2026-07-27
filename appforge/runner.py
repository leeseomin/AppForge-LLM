from __future__ import annotations

import json
import os
import threading
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .checkpoints import next_stage, read_checkpoint, write_checkpoint
from .constants import STAGE_RESULT_FILE_NAME
from .drivers import AgentDriver, DriverError
from .gates import (
    review_stage,
    run_declared_gates,
    validate_stage_artifacts,
    validate_stage_result,
)
from .llm_review import independent_llm_review
from .memory import append_stage_memory, failure_signature
from .models import PipelineSpec, ProjectLayout, StageSpec
from .pipelines import load_pipeline
from .projects import load_project, update_project
from .prompting import build_stage_prompt
from .util import atomic_write_json, atomic_write_text, utc_now

RunEventHandler = Callable[[dict[str, Any]], None]


@dataclass
class RunSummary:
    success: bool
    status: str
    completed_stages: list[str] = field(default_factory=list)
    failed_stage: str | None = None
    awaiting_stage: str | None = None
    message: str = ""
    failure: dict[str, Any] | None = None


class PipelineRunner:
    def __init__(
        self,
        layout: ProjectLayout,
        driver: AgentDriver,
        *,
        auto_approve: bool = True,
        approved_stage: str | None = None,
        allow_network: bool = False,
        allow_destructive: bool = False,
        max_stage_attempts: int | None = None,
        stage_timeout: int = 3600,
        event_handler: RunEventHandler | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.layout = layout
        self.driver = driver
        self.project = load_project(layout)
        self.pipeline: PipelineSpec = load_pipeline(str(self.project["pipeline"]))
        self.auto_approve = auto_approve
        self.approved_stage = approved_stage
        self.allow_network = allow_network
        self.allow_destructive = allow_destructive
        self.max_stage_attempts = max_stage_attempts or self.pipeline.max_stage_attempts
        self.stage_timeout = stage_timeout
        self.event_handler = event_handler
        self.cancel_event = cancel_event
        if hasattr(self.driver, "set_event_handler"):
            try:
                self.driver.set_event_handler(lambda payload: self._emit(str(payload.get("type") or "driver_event"), **{k: v for k, v in payload.items() if k != "type"}))
            except Exception:
                pass
        self.project = update_project(
            layout,
            {
                "last_run_at": utc_now(),
                "driver": driver.name,
                "safety": {
                    "allow_network": allow_network,
                    "allow_deploy": False,
                    "allow_destructive": allow_destructive,
                },
            },
        )

    def run(self, *, only_stage: str | None = None) -> RunSummary:
        completed: list[str] = []
        stage_name = only_stage or next_stage(self.layout, self.pipeline)
        self._emit(
            "pipeline_started",
            stage=stage_name,
            stage_count=len(self.pipeline.stages),
            stages=[stage.name for stage in self.pipeline.stages],
            driver=self.driver.name,
        )
        if stage_name is None:
            self._emit("pipeline_completed", completed_stages=[])
            return RunSummary(True, "completed", message="Pipeline is already complete")
        try:
            requested_stage = self.pipeline.stage(stage_name)
        except KeyError as exc:
            failure = {
                "code": "UNKNOWN_STAGE",
                "message": str(exc),
                "action": "Check the pipeline definition and project checkpoint state.",
                "stage": stage_name,
            }
            self._emit("pipeline_blocked", stage=stage_name, failure=failure)
            return RunSummary(
                False,
                "blocked",
                failed_stage=stage_name,
                message=str(exc),
                failure=failure,
            )
        if only_stage:
            prior = []
            for candidate in self.pipeline.stages:
                if candidate.name == requested_stage.name:
                    break
                checkpoint = read_checkpoint(self.layout, candidate.name)
                if not checkpoint or checkpoint.get("status") != "completed":
                    prior.append(candidate.name)
            if prior:
                message = (
                    f"Cannot run {stage_name} before prerequisite stages complete: "
                    f"{', '.join(prior)}"
                )
                failure = {
                    "code": "PREREQUISITE_STAGE_INCOMPLETE",
                    "message": message,
                    "action": "Complete the prerequisite stages, then retry this stage.",
                    "stage": stage_name,
                    "prerequisites": prior,
                }
                self._emit("pipeline_blocked", stage=stage_name, failure=failure)
                return RunSummary(
                    False,
                    "blocked",
                    failed_stage=stage_name,
                    message=message,
                    failure=failure,
                )

        existing = read_checkpoint(self.layout, stage_name)
        if existing and existing.get("status") == "awaiting_human":
            approved_by_user = self.approved_stage == stage_name
            if not self.auto_approve and not approved_by_user:
                failure = {
                    "code": "HUMAN_APPROVAL_REQUIRED",
                    "message": f"Stage {stage_name} awaits approval",
                    "action": "Approve the stage or rerun with automatic approval enabled.",
                    "stage": stage_name,
                }
                self._emit("stage_awaiting_approval", stage=stage_name, failure=failure)
                return RunSummary(
                    False,
                    "awaiting_human",
                    awaiting_stage=stage_name,
                    message=f"Stage {stage_name} awaits approval",
                    failure=failure,
                )
            write_checkpoint(
                self.layout,
                pipeline=self.pipeline,
                stage=stage_name,
                status="completed",
                attempt=int(existing.get("attempt", 1)),
                artifacts=existing.get("artifacts") or {},
                gates=existing.get("gates") or [],
                review=existing.get("review") or {},
                driver=existing.get("driver") or {},
                metadata={
                    **(existing.get("metadata") or {}),
                    "approved_at": utc_now(),
                    "approval_source": "automatic" if self.auto_approve else "human",
                },
            )
            if approved_by_user:
                self.approved_stage = None
            completed.append(stage_name)
            self._emit(
                "stage_completed",
                stage=stage_name,
                attempt=int(existing.get("attempt", 1)),
                auto_approved=self.auto_approve,
                approved_by_user=approved_by_user,
            )
            if only_stage:
                self._emit("pipeline_completed", completed_stages=completed)
                return RunSummary(
                    True,
                    "completed",
                    completed_stages=completed,
                    message=f"Approved {stage_name}",
                )
            stage_name = next_stage(self.layout, self.pipeline)

        while stage_name is not None:
            if self._cancelled():
                failure = self._cancel_failure(stage_name)
                self._emit("pipeline_cancelled", stage=stage_name, failure=failure)
                return RunSummary(
                    False,
                    "cancelled",
                    completed_stages=completed,
                    failed_stage=stage_name,
                    message=failure["message"],
                    failure=failure,
                )
            stage = self.pipeline.stage(stage_name)
            result = self._run_stage(stage)
            if not result["passed"]:
                summary = RunSummary(
                    False,
                    result["status"],
                    completed_stages=completed,
                    failed_stage=stage.name,
                    message=result["message"],
                    failure=result.get("failure"),
                )
                self._emit(
                    "pipeline_failed",
                    stage=stage.name,
                    completed_stages=completed,
                    failure=summary.failure,
                )
                return summary
            if result["status"] == "awaiting_human":
                failure = {
                    "code": "HUMAN_APPROVAL_REQUIRED",
                    "message": f"Stage {stage.name} completed and awaits approval",
                    "action": "Approve the stage to continue the pipeline.",
                    "stage": stage.name,
                }
                self._emit("stage_awaiting_approval", stage=stage.name, failure=failure)
                return RunSummary(
                    False,
                    "awaiting_human",
                    completed_stages=completed,
                    awaiting_stage=stage.name,
                    message=f"Stage {stage.name} completed and awaits approval",
                    failure=failure,
                )
            completed.append(stage.name)
            if only_stage:
                self._emit("pipeline_completed", completed_stages=completed)
                return RunSummary(
                    True,
                    "completed",
                    completed_stages=completed,
                    message=f"Stage {stage.name} completed",
                )
            stage_name = next_stage(self.layout, self.pipeline)

        atomic_write_text(
            self.layout.control / "COMPLETED.md",
            f"# Pipeline completed\n\nCompleted at {utc_now()} using `{self.driver.name}`.\n",
        )
        self._emit("pipeline_completed", completed_stages=completed)
        return RunSummary(
            True,
            "completed",
            completed_stages=completed,
            message="All pipeline stages completed",
        )

    def _run_stage(self, stage: StageSpec) -> dict[str, Any]:
        previous_failure: dict[str, Any] | None = None
        last_failure: dict[str, Any] | None = None
        seen_failure_signatures: set[str] = set()
        self._emit(
            "stage_started",
            stage=stage.name,
            description=stage.description,
            max_attempts=self.max_stage_attempts,
        )
        for attempt in range(1, self.max_stage_attempts + 1):
            if self._cancelled():
                failure = self._cancel_failure(stage.name, attempt=attempt)
                self._emit("stage_cancelled", stage=stage.name, attempt=attempt, failure=failure)
                return {
                    "passed": False,
                    "status": "cancelled",
                    "message": failure["message"],
                    "failure": failure,
                }
            self._emit(
                "attempt_started",
                stage=stage.name,
                attempt=attempt,
                max_attempts=self.max_stage_attempts,
            )
            prompt = build_stage_prompt(
                self.layout,
                project=self.project,
                pipeline=self.pipeline,
                stage=stage,
                attempt=attempt,
                previous_failure=previous_failure,
            )
            prompt_path = self.layout.prompts / f"{stage.name}-attempt-{attempt}.md"
            atomic_write_text(prompt_path, prompt)
            # A fresh completion record is mandatory for every attempt. Artifacts may be
            # refined in place, but a stale success record must never validate a new run.
            (self.layout.control / STAGE_RESULT_FILE_NAME).unlink(missing_ok=True)
            write_checkpoint(
                self.layout,
                pipeline=self.pipeline,
                stage=stage.name,
                status="in_progress",
                attempt=attempt,
                metadata={"prompt": str(prompt_path), "started_at": utc_now()},
            )
            self._emit(
                "agent_started",
                stage=stage.name,
                attempt=attempt,
                driver=self.driver.name,
            )
            try:
                driver_result = self.driver.run(
                    prompt,
                    layout=self.layout,
                    stage=stage.name,
                    attempt=attempt,
                    timeout=self.stage_timeout,
                    cancel_event=self.cancel_event,
                )
            except DriverError as exc:
                driver_result_dict = {
                    "success": False,
                    "error": str(exc),
                    "command": [],
                }
                last_failure = {
                    "code": "DRIVER_ERROR",
                    "message": str(exc),
                    "action": (
                        "Verify that the selected external LLM provider, API key, and bridge "
                        "URL are configured, then retry."
                    ),
                    "stage": stage.name,
                    "attempt": attempt,
                    "driver": {"name": self.driver.name, **driver_result_dict},
                }
                previous_failure = {
                    "repair_mode": "repair",
                    "driver": driver_result_dict,
                    "driver_error": str(exc),
                    "failed_checks": [],
                    "files_changed": [],
                }
                attempt_log = {
                    "stage": stage.name,
                    "attempt": attempt,
                    "passed": False,
                    "driver": driver_result_dict,
                    "failure": last_failure,
                    "timestamp": utc_now(),
                }
                stop_retrying = self._register_failure_signature(
                    stage=stage,
                    attempt=attempt,
                    seen=seen_failure_signatures,
                    failure=last_failure,
                )
                if last_failure.get("next_retry_mode"):
                    previous_failure["repair_mode"] = last_failure.get("next_retry_mode")
                    previous_failure["next_retry_mode"] = last_failure.get("next_retry_mode")
                attempt_log["failure"] = last_failure
                self._write_attempt_log(stage.name, attempt, attempt_log)
                self._remember_attempt(
                    stage=stage,
                    attempt=attempt,
                    passed=False,
                    stage_result={},
                    artifact_paths={},
                    records=[],
                    review={},
                    failure=last_failure,
                )
                write_checkpoint(
                    self.layout,
                    pipeline=self.pipeline,
                    stage=stage.name,
                    status="failed",
                    attempt=attempt,
                    driver=driver_result_dict,
                    metadata={"driver_error": str(exc)},
                )
                self._emit(
                    "attempt_failed",
                    stage=stage.name,
                    attempt=attempt,
                    failure=last_failure,
                )
                if stop_retrying:
                    break
                if attempt < self.max_stage_attempts:
                    self._emit(
                        "stage_retrying",
                        stage=stage.name,
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        failure=last_failure,
                    )
                continue

            self._emit(
                "agent_completed",
                stage=stage.name,
                attempt=attempt,
                driver=self.driver.name,
                success=driver_result.success,
                exit_code=driver_result.exit_code,
                duration_seconds=driver_result.duration_seconds,
            )
            if self._cancelled():
                failure = self._cancel_failure(stage.name, attempt=attempt)
                self._emit("stage_cancelled", stage=stage.name, attempt=attempt, failure=failure)
                return {
                    "passed": False,
                    "status": "cancelled",
                    "message": failure["message"],
                    "failure": failure,
                }
            self._emit("validation_started", stage=stage.name, attempt=attempt)
            stage_record_ok, stage_result, stage_record_error = validate_stage_result(
                self.layout, stage
            )
            artifacts_ok, artifact_records, artifact_paths = validate_stage_artifacts(
                self.layout, stage
            )
            gates_ok, gate_records = run_declared_gates(
                self.layout,
                stage=stage,
                allow_network=self.allow_network,
                allow_destructive=self.allow_destructive,
            )
            records = artifact_records + gate_records
            if not stage_record_ok:
                records.insert(
                    0,
                    {
                        "kind": "completion_record",
                        "name": "stage-result",
                        "required": True,
                        "passed": False,
                        "error": stage_record_error,
                    },
                )
            review = review_stage(stage, records, stage_result)
            if (
                driver_result.success
                and stage_record_ok
                and artifacts_ok
                and gates_ok
                and review["passed"]
            ):
                independent_review = independent_llm_review(
                    self.layout,
                    stage=stage,
                    stage_result=stage_result,
                    records=records,
                    driver=self.driver,
                    timeout=min(float(self.stage_timeout), 180.0),
                )
                review["independent_llm_review"] = independent_review
                if independent_review.get("verdict") == "block":
                    review["passed"] = False
                    review.setdefault("findings", []).extend(independent_review.get("findings") or [])
                    review["critical_count"] = int(review.get("critical_count") or 0) + 1
                    review["finding_count"] = int(review.get("finding_count") or 0) + len(independent_review.get("findings") or [])
                elif independent_review.get("verdict") == "concerns":
                    review.setdefault("findings", []).extend(independent_review.get("findings") or [])
                    review["finding_count"] = int(review.get("finding_count") or 0) + len(independent_review.get("findings") or [])
            passed = (
                driver_result.success
                and stage_record_ok
                and artifacts_ok
                and gates_ok
                and review["passed"]
            )
            last_failure = None if passed else self._build_failure(
                stage=stage,
                attempt=attempt,
                driver_result=driver_result.to_dict(),
                stage_record_error=stage_record_error,
                records=records,
                review=review,
            )
            stop_retrying = False
            if last_failure is not None:
                stop_retrying = self._register_failure_signature(
                    stage=stage,
                    attempt=attempt,
                    seen=seen_failure_signatures,
                    failure=last_failure,
                )
            attempt_log = {
                "stage": stage.name,
                "attempt": attempt,
                "passed": passed,
                "driver": driver_result.to_dict(),
                "stage_result": stage_result,
                "records": records,
                "review": review,
                "failure": last_failure,
                "timestamp": utc_now(),
            }
            self._write_attempt_log(stage.name, attempt, attempt_log)

            if passed:
                self._remember_attempt(
                    stage=stage,
                    attempt=attempt,
                    passed=True,
                    stage_result=stage_result,
                    artifact_paths=artifact_paths,
                    records=records,
                    review=review,
                    failure=None,
                )
                status = (
                    "awaiting_human"
                    if stage.approval
                    and self.project.get("mode") == "guided"
                    and not self.auto_approve
                    else "completed"
                )
                write_checkpoint(
                    self.layout,
                    pipeline=self.pipeline,
                    stage=stage.name,
                    status=status,
                    attempt=attempt,
                    artifacts=artifact_paths,
                    gates=records,
                    review=review,
                    driver=driver_result.to_dict(),
                    metadata={"stage_result": stage_result},
                )
                self._emit(
                    "stage_completed",
                    stage=stage.name,
                    attempt=attempt,
                    status=status,
                    artifacts=artifact_paths,
                )
                return {
                    "passed": True,
                    "status": status,
                    "message": f"Stage {stage.name} passed",
                }

            previous_failure = {
                "repair_mode": "repair",
                "driver_success": driver_result.success,
                "driver_error": driver_result.stderr or driver_result.stdout,
                "stage_result_error": stage_record_error,
                "records": records,
                "review": review,
                "failed_checks": (last_failure or {}).get("failed_checks") or [],
                "review_findings": (last_failure or {}).get("review_findings") or [],
                "files_changed": stage_result.get("files_changed") or [],
                "failure_evidence": (last_failure or {}).get("failure_evidence") or "",
                "next_retry_mode": (last_failure or {}).get("next_retry_mode") or "repair",
            }
            if previous_failure.get("next_retry_mode"):
                previous_failure["repair_mode"] = str(previous_failure["next_retry_mode"])
            write_checkpoint(
                self.layout,
                pipeline=self.pipeline,
                stage=stage.name,
                status="failed",
                attempt=attempt,
                artifacts=artifact_paths,
                gates=records,
                review=review,
                driver=driver_result.to_dict(),
                metadata={"stage_result": stage_result},
            )
            self._remember_attempt(
                stage=stage,
                attempt=attempt,
                passed=False,
                stage_result=stage_result,
                artifact_paths=artifact_paths,
                records=records,
                review=review,
                failure=last_failure,
            )
            self._emit(
                "attempt_failed",
                stage=stage.name,
                attempt=attempt,
                failure=last_failure,
            )
            if stop_retrying:
                break
            if attempt < self.max_stage_attempts:
                self._emit(
                    "stage_retrying",
                    stage=stage.name,
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    failure=last_failure,
                )

        failure = last_failure or {
            "code": "STAGE_FAILED",
            "message": f"Stage {stage.name} did not pass.",
            "action": "Open the stage attempt log, fix the reported issue, and retry.",
            "stage": stage.name,
            "attempt": self.max_stage_attempts,
        }
        final_attempt = int(failure.get("attempt") or self.max_stage_attempts)
        self._emit(
            "stage_failed",
            stage=stage.name,
            attempt=final_attempt,
            failure=failure,
        )
        return {
            "passed": False,
            "status": "failed",
            "message": f"Stage {stage.name} failed after {final_attempt} attempts",
            "failure": failure,
        }

    def _register_failure_signature(
        self,
        *,
        stage: StageSpec,
        attempt: int,
        seen: set[str],
        failure: dict[str, Any],
    ) -> bool:
        signature = failure_signature(failure)
        if signature in seen:
            previous_code = str(failure.get("code") or "STAGE_FAILED")
            failure.update(
                {
                    "code": "REPEATED_FAILURE_LOOP",
                    "message": (
                        f"Stage {stage.name} produced the same failing signature again "
                        f"on attempt {attempt}."
                    ),
                    "action": (
                        "Stop repeating the same repair path. Re-read the failed checks, change "
                        "the implementation strategy, simplify the scope, or run the exact failing "
                        "command locally before retrying."
                    ),
                    "previous_code": previous_code,
                    "loop_signature": signature,
                    "next_retry_mode": "regenerate",
                    "repair_mode": "regenerate",
                }
            )
            self._emit(
                "loop_guard_triggered",
                stage=stage.name,
                attempt=attempt,
                failure=failure,
            )
            return True
        seen.add(signature)
        failure["loop_signature"] = signature
        return False

    def _remember_attempt(
        self,
        *,
        stage: StageSpec,
        attempt: int,
        passed: bool,
        stage_result: dict[str, Any],
        artifact_paths: dict[str, str],
        records: list[dict[str, Any]],
        review: dict[str, Any],
        failure: dict[str, Any] | None,
    ) -> None:
        failed_checks: list[dict[str, Any]] = []
        for record in records:
            if record.get("passed"):
                continue
            result = record.get("result") or {}
            data = result.get("data") or {}
            failed_checks.append(
                {
                    "kind": record.get("kind"),
                    "name": record.get("name"),
                    "required": bool(record.get("required")),
                    "reason": record.get("error") or result.get("error") or data.get("reason"),
                }
            )
        payload = {
            "stage": stage.name,
            "attempt": attempt,
            "status": "completed" if passed else "failed",
            "summary": stage_result.get("summary")
            or (failure or {}).get("message")
            or f"Stage {stage.name} {'passed' if passed else 'failed'}",
            "artifact_names": list(artifact_paths.keys()),
            "artifact_paths": artifact_paths,
            "checks_total": len(records),
            "failed_checks": failed_checks[:12],
            "review": {
                "passed": review.get("passed"),
                "critical_count": review.get("critical_count"),
                "finding_count": review.get("finding_count"),
            },
            "unresolved": stage_result.get("unresolved") or [],
            "decisions": stage_result.get("decisions") or [],
            "failure": failure,
        }
        try:
            append_stage_memory(self.layout, payload)
        except Exception:
            # Memory improves later stage quality, but loss of telemetry must not fail work.
            return

    def _build_failure(
        self,
        *,
        stage: StageSpec,
        attempt: int,
        driver_result: dict[str, Any],
        stage_record_error: str | None,
        records: list[dict[str, Any]],
        review: dict[str, Any],
    ) -> dict[str, Any]:
        failed_checks: list[dict[str, Any]] = []
        for record in records:
            if record.get("passed"):
                continue
            result = record.get("result") or {}
            data = result.get("data") or {}
            reason = (
                record.get("error")
                or result.get("error")
                or data.get("reason")
                or "Check did not pass"
            )
            item: dict[str, Any] = {
                "kind": record.get("kind"),
                "name": record.get("name"),
                "required": bool(record.get("required")),
                "reason": str(reason),
            }
            if data.get("checks"):
                item["checks"] = data["checks"]
            if result.get("command"):
                item["command"] = result["command"]
            evidence = _failure_evidence_tail(result, data)
            if evidence:
                item["evidence"] = evidence
                implicated = _extract_implicated_files("\n".join(str(value) for value in evidence.values()), self.layout)
                if implicated:
                    item["implicated_files"] = implicated
            failed_checks.append(item)

        if not driver_result.get("success"):
            code = "AGENT_PROCESS_FAILED"
            message = (
                f"The coding agent exited with code {driver_result.get('exit_code')} "
                f"during stage {stage.name}."
            )
            action = (
                "Review the agent output below, correct authentication, permissions, or the "
                "reported implementation error, then retry."
            )
        elif stage_record_error:
            code = "STAGE_RESULT_INVALID"
            message = stage_record_error
            action = (
                "The coding agent must write a fresh, schema-valid .appforge/stage-result.json "
                "for this stage before it can pass."
            )
        elif failed_checks:
            code = "STAGE_CHECK_FAILED"
            required_names = [
                str(item.get("name")) for item in failed_checks if item.get("required")
            ]
            message = (
                f"Required validation failed in stage {stage.name}: "
                f"{', '.join(required_names) or 'review findings'}"
            )
            action = (
                "Fix the failed artifact, test, build, security, or release check shown below; "
                "the pipeline will retry the stage automatically when attempts remain."
            )
        else:
            code = "STAGE_REVIEW_FAILED"
            message = f"The stage review did not pass for {stage.name}."
            action = "Resolve the critical review findings and retry the stage."

        findings = [
            item
            for item in (review.get("findings") or [])
            if item.get("severity") == "critical"
        ]
        return {
            "code": code,
            "message": message,
            "action": action,
            "stage": stage.name,
            "attempt": attempt,
            "driver": {
                "name": self.driver.name,
                "success": bool(driver_result.get("success")),
                "exit_code": driver_result.get("exit_code"),
                "duration_seconds": driver_result.get("duration_seconds"),
                "stdout": driver_result.get("stdout") or "",
                "stderr": driver_result.get("stderr") or "",
                "command": driver_result.get("command") or [],
            },
            "stage_result_error": stage_record_error,
            "failed_checks": failed_checks,
            "review_findings": findings,
        }


    def _cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    def _cancel_failure(self, stage: str | None, *, attempt: int | None = None) -> dict[str, Any]:
        return {
            "code": "JOB_CANCELLED",
            "message": "사용자가 실행 중인 작업을 취소했습니다.",
            "action": "필요하면 새 요청을 시작하세요.",
            "stage": stage,
            "attempt": attempt,
        }

    def _emit(self, event: str, **payload: Any) -> None:
        if self.event_handler is None:
            return
        message = {
            "event": event,
            "timestamp": utc_now(),
            "pipeline": self.pipeline.name,
            "project": self.project.get("name"),
            **payload,
        }
        try:
            self.event_handler(message)
        except Exception:
            # Status reporting must never make the production pipeline fail.
            return

    def _write_attempt_log(self, stage: str, attempt: int, payload: dict[str, Any]) -> None:
        atomic_write_json(self.layout.logs / f"{stage}-attempt-{attempt}.json", payload)

def _failure_evidence_tail(result: dict[str, Any], data: dict[str, Any], *, limit: int = 8_000) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for key in ("stdout", "stderr"):
        value = data.get(key) or result.get(key)
        if isinstance(value, str) and value.strip():
            evidence[f"{key}_tail"] = value[-limit:]
    error = result.get("error")
    if isinstance(error, str) and error.strip():
        evidence["error_tail"] = error[-limit:]
    return evidence


def _extract_implicated_files(text: str, layout: ProjectLayout, *, max_files: int = 8) -> list[str]:
    matches: list[str] = []
    for raw in re.findall(r"(?<![\w.-])([A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx|vue|css|html|json|yaml|yml|toml|rs|go|java|kt|swift|dart))(?:[:(]\d+)?", text):
        rel = raw.replace("\\", "/").lstrip("./")
        if rel.startswith(".appforge/") or ".." in rel.split("/"):
            continue
        path = (layout.root / rel).resolve()
        try:
            path.relative_to(layout.root.resolve())
        except ValueError:
            continue
        if path.exists() and rel not in matches:
            matches.append(rel)
            if len(matches) >= max_files:
                break
    return matches
