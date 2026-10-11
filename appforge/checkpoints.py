from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import STATE_FILE_NAME
from .models import PipelineSpec, ProjectLayout
from .util import atomic_write_json, read_json, utc_now
from .evidence import POLICY_VERSION, artifact_digests, policy_digest, read_host, write_host
from .snapshots import digest, source_manifest

VALID_STATUSES = {"in_progress", "completed", "awaiting_human", "failed", "blocked"}


def checkpoint_path(layout: ProjectLayout, stage: str) -> Path:
    return layout.checkpoints / f"checkpoint_{stage}.json"


def write_checkpoint(
    layout: ProjectLayout,
    *,
    pipeline: PipelineSpec,
    stage: str,
    status: str,
    attempt: int,
    artifacts: dict[str, str] | None = None,
    gates: list[dict[str, Any]] | None = None,
    review: dict[str, Any] | None = None,
    driver: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid checkpoint status: {status}")
    pipeline.stage(stage)
    payload = {
        "version": "1.0",
        "pipeline": pipeline.name,
        "pipeline_version": pipeline.version,
        "stage": stage,
        "status": status,
        "attempt": attempt,
        "timestamp": utc_now(),
        "artifacts": artifacts or {},
        "gates": gates or [],
        "review": review or {},
        "driver": driver or {},
        "metadata": metadata or {},
        "verification_policy": POLICY_VERSION,
        "policy_digest": policy_digest(pipeline),
        "source_snapshot": digest(source_manifest(layout.root)),
        "artifact_digests": artifact_digests(layout.root, pipeline.stage(stage).produces),
    }
    path = checkpoint_path(layout, stage)
    # Retain review history, but a rerun must never reuse later completions.
    if status == "in_progress":
        index = [item.name for item in pipeline.stages].index(stage)
        for later in pipeline.stages[index + 1:]:
            stale = read_checkpoint(layout, later.name)
            if stale:
                stale = {**stale, "status": "stale", "invalidated_by": stage}
                atomic_write_json(checkpoint_path(layout, later.name), stale)
                write_host(layout.root, checkpoint_path(layout, later.name).name, stale)
    atomic_write_json(path, payload)
    write_host(layout.root, path.name, payload)
    _update_state(layout, pipeline, stage, status)
    return path


def read_checkpoint(layout: ProjectLayout, stage: str) -> dict[str, Any] | None:
    data = read_json(checkpoint_path(layout, stage))
    return data if isinstance(data, dict) else None


def checkpoint_is_current(layout: ProjectLayout, pipeline: PipelineSpec, stage: str,
                          *, check_source: bool = False) -> bool:
    cp = read_checkpoint(layout, stage)
    return bool(cp and cp == read_host(layout.root, checkpoint_path(layout, stage).name)
                and cp.get("pipeline_version") == pipeline.version
                and cp.get("policy_digest") == policy_digest(pipeline)
                and cp.get("artifact_digests") == artifact_digests(layout.root, pipeline.stage(stage).produces)
                and (not check_source or cp.get("source_snapshot") == digest(source_manifest(layout.root))))


def completed_stages(layout: ProjectLayout, pipeline: PipelineSpec) -> list[str]:
    completed: list[str] = []
    for stage in pipeline.stages:
        cp = read_checkpoint(layout, stage.name)
        if cp and cp.get("status") == "completed" and checkpoint_is_current(layout, pipeline, stage.name):
            completed.append(stage.name)
        else:
            break
    return completed


def next_stage(layout: ProjectLayout, pipeline: PipelineSpec) -> str | None:
    completed = set(completed_stages(layout, pipeline))
    ordered = [stage for stage in pipeline.stages if stage.name in completed]
    if ordered:
        latest = read_checkpoint(layout, ordered[-1].name) or {}
        if latest.get("source_snapshot") != digest(source_manifest(layout.root)):
            for stage in ordered:
                if stage.name in {"verification", "regression"}:
                    return stage.name
    for stage in pipeline.stages:
        if stage.name not in completed:
            return stage.name
    if pipeline.name in {"web-app", "web-app-lite"}:
        from .validation_service import archive_verification, current_receipt
        if not current_receipt(layout):
            return "verification"
        for stage in reversed(pipeline.stages):
            cp = read_checkpoint(layout, stage.name) or {}
            for gate in cp.get("gates", []):
                if gate.get("name") == "archive_workspace":
                    archive = (gate.get("result") or {}).get("data", {}).get("archive")
                    if not archive or archive_verification(layout, layout.root / archive).get("status") != "verified":
                        return stage.name
    return None


def _update_state(layout: ProjectLayout, pipeline: PipelineSpec, stage: str, status: str) -> None:
    completed = completed_stages(layout, pipeline)
    current: str | None = None
    for spec in pipeline.stages:
        if spec.name not in completed:
            current = spec.name
            break
    overall = "completed" if current is None else status
    atomic_write_json(
        layout.control / STATE_FILE_NAME,
        {
            "version": "1.0",
            "status": overall,
            "current_stage": current,
            "completed_stages": completed,
            "updated_at": utc_now(),
        },
    )
