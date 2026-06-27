from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import STATE_FILE_NAME
from .models import PipelineSpec, ProjectLayout
from .util import atomic_write_json, read_json, utc_now

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
    }
    path = checkpoint_path(layout, stage)
    atomic_write_json(path, payload)
    _update_state(layout, pipeline, stage, status)
    return path


def read_checkpoint(layout: ProjectLayout, stage: str) -> dict[str, Any] | None:
    data = read_json(checkpoint_path(layout, stage))
    return data if isinstance(data, dict) else None


def completed_stages(layout: ProjectLayout, pipeline: PipelineSpec) -> list[str]:
    completed: list[str] = []
    for stage in pipeline.stages:
        cp = read_checkpoint(layout, stage.name)
        if cp and cp.get("status") == "completed":
            completed.append(stage.name)
    return completed


def next_stage(layout: ProjectLayout, pipeline: PipelineSpec) -> str | None:
    completed = set(completed_stages(layout, pipeline))
    for stage in pipeline.stages:
        if stage.name not in completed:
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
