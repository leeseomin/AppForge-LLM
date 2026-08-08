from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import ProjectLayout
from .util import redact, truncate, utc_now

MEMORY_FILE_NAME = "stage-memory.jsonl"
MAX_MEMORY_ENTRY_CHARS = 8_000
MAX_RENDERED_MEMORY_CHARS = 18_000


def memory_log_path(layout: ProjectLayout) -> Path:
    return layout.memory / MEMORY_FILE_NAME


def append_stage_memory(layout: ProjectLayout, payload: dict[str, Any]) -> Path:
    """Append a compact, redacted stage-memory event for later attempts/stages."""
    layout.memory.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema_version": "1.0",
        "timestamp": utc_now(),
        **payload,
    }
    rendered = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    if len(rendered) > MAX_MEMORY_ENTRY_CHARS:
        entry["truncated"] = True
        entry["payload_preview"] = truncate(rendered, MAX_MEMORY_ENTRY_CHARS)
        rendered = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with memory_log_path(layout).open("a", encoding="utf-8") as handle:
        handle.write(redact(rendered) + "\n")
    return memory_log_path(layout)


def read_stage_memory(layout: ProjectLayout, *, limit: int = 20) -> list[dict[str, Any]]:
    path = memory_log_path(layout)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            entries.append(data)
    return entries[-limit:]


def _artifact_preview(layout: ProjectLayout, artifact: str, *, limit: int = 6_000) -> str | None:
    path = layout.artifacts / f"{artifact}.json"
    if not path.exists():
        return None
    return truncate(path.read_text(encoding="utf-8", errors="replace"), limit)


def render_memory_context(layout: ProjectLayout, *, current_stage: str) -> str:
    """Render persistent engineering memory for the next agent packet."""
    sections: list[str] = []
    entries = read_stage_memory(layout, limit=12)
    if entries:
        lines = []
        for item in entries:
            if item.get("stage") == current_stage and item.get("status") == "in_progress":
                continue
            summary = item.get("summary") or item.get("message") or item.get("code") or "memory event"
            stage = item.get("stage") or "pipeline"
            status = item.get("status") or "recorded"
            attempt = item.get("attempt")
            suffix = f" attempt={attempt}" if attempt else ""
            lines.append(f"- {stage} [{status}{suffix}]: {summary}")
            failed = item.get("failed_checks") or []
            for check in failed[:4]:
                name = check.get("name") if isinstance(check, dict) else None
                reason = check.get("reason") if isinstance(check, dict) else None
                lines.append(f"  - failed {name or 'check'}: {truncate(str(reason or ''), 240)}")
        sections.append("## Stage memory ledger\n" + "\n".join(lines))

    for artifact, title in (
        ("requirements_spec", "Specification contract"),
        ("workflow_spec", "Workflow contract"),
        ("memory_spec", "Memory contract"),
        ("loop_spec", "Loop contract"),
    ):
        preview = _artifact_preview(layout, artifact)
        if preview:
            sections.append(f"## {title}\n```json\n{preview}\n```")

    rendered = "\n\n".join(sections) or "<none yet>"
    return truncate(rendered, MAX_RENDERED_MEMORY_CHARS)


_DRIVER_ERROR_CODE = re.compile(r"^([A-Z][A-Z0-9_]{4,}):")


def driver_error_code(driver: Any) -> str | None:
    """Pull the machine-readable code out of a driver's stderr, when it has one."""
    if not isinstance(driver, dict):
        return None
    first_line = str(driver.get("stderr") or "").strip().splitlines()[:1]
    if not first_line:
        return None
    match = _DRIVER_ERROR_CODE.match(first_line[0].strip())
    return match.group(1) if match else None


def failure_signature(failure: dict[str, Any]) -> str:
    """Create a stable signature for detecting unproductive retry loops."""
    compact = {
        "code": failure.get("code"),
        "stage_result_error": failure.get("stage_result_error"),
        "failed_checks": [
            {
                "kind": item.get("kind"),
                "name": item.get("name"),
                "required": item.get("required"),
                "reason": item.get("reason"),
            }
            for item in failure.get("failed_checks", [])
            if isinstance(item, dict)
        ],
        "review_findings": [
            {
                "severity": item.get("severity"),
                "criterion": item.get("criterion"),
                "finding": item.get("finding"),
            }
            for item in failure.get("review_findings", [])
            if isinstance(item, dict)
        ],
        "driver_exit_code": (failure.get("driver") or {}).get("exit_code")
        if isinstance(failure.get("driver"), dict)
        else None,
        # Two attempts that died for different reasons are not a loop, even when they
        # produce the same exit code and the same (empty) set of failed checks.
        "driver_error_code": driver_error_code(failure.get("driver")),
        "submitted_artifacts": sorted(failure.get("submitted_artifacts") or []),
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True)
