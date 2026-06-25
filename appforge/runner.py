from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checkpoints import next_stage, read_checkpoint, write_checkpoint
from .drivers import AgentDriver, DriverError
from .gates import review_stage, run_declared_gates, validate_stage_artifacts, validate_stage_result
from .models import PipelineSpec, ProjectLayout, StageSpec
from .pipelines import load_pipeline
from .projects import load_project, update_project
from .prompting import build_stage_prompt
from .constants import STAGE_RESULT_FILE_NAME
from .util import atomic_write_json, atomic_write_text, utc_now


@dataclass
class RunSummary:
    success: bool
    status: str
    completed_stages: list[str] = field(default_factory=list)
    failed_stage: str | None = None
    awaiting_stage: str | None = None
    message: str = ""


class PipelineRunner:
    def __init__(
        self,
        layout: ProjectLayout,
        driver: AgentDriver,
        *,
        auto_approve: bool = True,
        allow_network: bool = False,
        allow_destructive: bool = False,
        max_stage_attempts: int | None = None,
        stage_timeout: int = 3600,
    ) -> None:
        self.layout = layout
        self.driver = driver
        self.project = load_project(layout)
        self.pipeline: PipelineSpec = load_pipeline(str(self.project["pipeline"]))
        self.auto_approve = auto_approve
        self.allow_network = allow_network
        self.allow_destructive = allow_destructive
        self.max_stage_attempts = max_stage_attempts or self.pipeline.max_stage_attempts
        self.stage_timeout = stage_timeout
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
        if stage_name is None:
            return RunSummary(True, "completed", message="Pipeline is already complete")
        try:
            requested_stage = self.pipeline.stage(stage_name)
        except KeyError as exc:
            return RunSummary(False, "blocked", failed_stage=stage_name, message=str(exc))
        if only_stage:
            prior = []
            for candidate in self.pipeline.stages:
                if candidate.name == requested_stage.name:
                    break
                checkpoint = read_checkpoint(self.layout, candidate.name)
                if not checkpoint or checkpoint.get("status") != "completed":
                    prior.append(candidate.name)
            if prior:
                return RunSummary(
                    False,
                    "blocked",
                    failed_stage=stage_name,
                    message=f"Cannot run {stage_name} before prerequisite stages complete: {', '.join(prior)}",
                )

        existing = read_checkpoint(self.layout, stage_name)
        if existing and existing.get("status") == "awaiting_human":
            if not self.auto_approve:
                return RunSummary(False, "awaiting_human", awaiting_stage=stage_name, message=f"Stage {stage_name} awaits approval")
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
                metadata={**(existing.get("metadata") or {}), "auto_approved_at": utc_now()},
            )
            completed.append(stage_name)
            if only_stage:
                return RunSummary(True, "completed", completed_stages=completed, message=f"Approved {stage_name}")
            stage_name = next_stage(self.layout, self.pipeline)

        while stage_name is not None:
            stage = self.pipeline.stage(stage_name)
            result = self._run_stage(stage)
            if not result["passed"]:
                return RunSummary(
                    False,
                    result["status"],
                    completed_stages=completed,
                    failed_stage=stage.name,
                    message=result["message"],
                )
            if result["status"] == "awaiting_human":
                return RunSummary(
                    False,
                    "awaiting_human",
                    completed_stages=completed,
                    awaiting_stage=stage.name,
                    message=f"Stage {stage.name} completed and awaits approval",
                )
            completed.append(stage.name)
            if only_stage:
                return RunSummary(True, "completed", completed_stages=completed, message=f"Stage {stage.name} completed")
            stage_name = next_stage(self.layout, self.pipeline)

        atomic_write_text(
            self.layout.control / "COMPLETED.md",
            f"# Pipeline completed\n\nCompleted at {utc_now()} using `{self.driver.name}`.\n",
        )
        return RunSummary(True, "completed", completed_stages=completed, message="All pipeline stages completed")

    def _run_stage(self, stage: StageSpec) -> dict[str, Any]:
        previous_failure: dict[str, Any] | None = None
        for attempt in range(1, self.max_stage_attempts + 1):
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
            try:
                driver_result = self.driver.run(
                    prompt,
                    layout=self.layout,
                    stage=stage.name,
                    attempt=attempt,
                    timeout=self.stage_timeout,
                )
            except DriverError as exc:
                driver_result_dict = {"success": False, "error": str(exc), "command": []}
                previous_failure = {"driver": driver_result_dict}
                attempt_log = {
                    "stage": stage.name,
                    "attempt": attempt,
                    "passed": False,
                    "driver": driver_result_dict,
                    "timestamp": utc_now(),
                }
                self._write_attempt_log(stage.name, attempt, attempt_log)
                write_checkpoint(
                    self.layout,
                    pipeline=self.pipeline,
                    stage=stage.name,
                    status="failed",
                    attempt=attempt,
                    driver=driver_result_dict,
                    metadata={"driver_error": str(exc)},
                )
                continue

            stage_record_ok, stage_result, stage_record_error = validate_stage_result(self.layout, stage)
            artifacts_ok, artifact_records, artifact_paths = validate_stage_artifacts(self.layout, stage)
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
            passed = driver_result.success and stage_record_ok and artifacts_ok and gates_ok and review["passed"]
            attempt_log = {
                "stage": stage.name,
                "attempt": attempt,
                "passed": passed,
                "driver": driver_result.to_dict(),
                "stage_result": stage_result,
                "records": records,
                "review": review,
                "timestamp": utc_now(),
            }
            self._write_attempt_log(stage.name, attempt, attempt_log)

            if passed:
                status = "awaiting_human" if stage.approval and self.project.get("mode") == "guided" and not self.auto_approve else "completed"
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
                return {"passed": True, "status": status, "message": f"Stage {stage.name} passed"}

            previous_failure = {
                "driver_success": driver_result.success,
                "driver_error": driver_result.stderr or driver_result.stdout,
                "stage_result_error": stage_record_error,
                "records": records,
                "review": review,
            }
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

        return {
            "passed": False,
            "status": "failed",
            "message": f"Stage {stage.name} failed after {self.max_stage_attempts} attempts",
        }

    def _write_attempt_log(self, stage: str, attempt: int, payload: dict[str, Any]) -> None:
        atomic_write_json(self.layout.logs / f"{stage}-attempt-{attempt}.json", payload)
