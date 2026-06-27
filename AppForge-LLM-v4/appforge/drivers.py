from __future__ import annotations

import json
import re
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .artifacts import ArtifactValidationError, load_artifact_schema, validate_artifact
from .constants import MAX_CAPTURE_CHARS, STAGE_RESULT_FILE_NAME
from .models import DriverResult, ProjectLayout
from .pipelines import load_pipeline
from .projects import load_project
from .util import atomic_write_json, atomic_write_text, redact, truncate

DEFAULT_LLM_BRIDGE_URL = "http://127.0.0.1:8788"


class DriverError(RuntimeError):
    pass


class AgentDriver(ABC):
    name: str

    @abstractmethod
    def run(
        self,
        prompt: str,
        *,
        layout: ProjectLayout,
        stage: str,
        attempt: int,
        timeout: int,
        cancel_event: threading.Event | None = None,
    ) -> DriverResult:
        raise NotImplementedError


def _redact_text(text: str) -> str:
    return redact(truncate(text, MAX_CAPTURE_CHARS))


def _find_outermost_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise DriverError("LLM bridge returned an empty response")
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL)
    if fence:
        try:
            payload = json.loads(fence.group(1))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    scan = stripped
    while True:
        candidate = _find_outermost_json_object(scan)
        if not candidate:
            break
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            brace_start = scan.find("{")
            next_start = scan.find("{", brace_start + 1)
            if next_start < 0:
                raise DriverError(f"LLM bridge response was not valid JSON: invalid object at position {brace_start}") from None
            scan = scan[next_start:]
            continue
    raise DriverError("LLM bridge response did not contain a JSON object")


def _safe_workspace_path(layout: ProjectLayout, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or not candidate.parts:
        raise DriverError(f"Unsafe file path from LLM bridge: {relative_path!r}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise DriverError(f"Unsafe file path from LLM bridge: {relative_path!r}")
    if candidate.parts[0] in {".git", ".hg", ".svn", ".appforge"}:
        raise DriverError(f"LLM bridge cannot write managed path: {relative_path!r}")
    resolved = (layout.root / candidate).resolve()
    try:
        resolved.relative_to(layout.root)
    except ValueError as exc:
        raise DriverError(f"Unsafe file path from LLM bridge: {relative_path!r}") from exc
    return resolved


def _iter_file_payloads(files: Any) -> list[tuple[str, str]]:
    if files in (None, {}, []):
        return []
    items: list[tuple[str, str]] = []
    if isinstance(files, dict):
        for path, content in files.items():
            if not isinstance(path, str) or not isinstance(content, str):
                raise DriverError("LLM bridge files object must map string paths to string content")
            items.append((path, content))
        return items
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                raise DriverError("LLM bridge files list entries must be objects")
            path = item.get("path")
            content = item.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise DriverError("LLM bridge file entries require string path and content")
            items.append((path, content))
        return items
    raise DriverError("LLM bridge files must be an object or list")


def _write_bridge_files(layout: ProjectLayout, files: Any) -> list[str]:
    changed: list[str] = []
    for relative_path, content in _iter_file_payloads(files):
        if _is_bridge_managed_output_path(relative_path):
            continue
        path = _safe_workspace_path(layout, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        changed.append(str(path.relative_to(layout.root)))
    return changed


def _is_bridge_managed_output_path(relative_path: str) -> bool:
    candidate = Path(relative_path)
    if candidate.parts == (".appforge", STAGE_RESULT_FILE_NAME):
        return True
    return (
        len(candidate.parts) == 3
        and candidate.parts[0] == ".appforge"
        and candidate.parts[1] == "artifacts"
        and candidate.suffix == ".json"
    )


def _default_stage_result(stage: str, changed: list[str]) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": "completed",
        "summary": "Generated by the external LLM bridge.",
        "files_changed": changed,
        "commands_run": [
            {
                "command": "llm-bridge /generate",
                "result": "Parsed JSON envelope and wrote requested files/artifacts.",
            }
        ],
        "checks": [
            {
                "name": "llm-bridge-json-envelope",
                "passed": True,
                "evidence": "Response parsed and required artifact schemas were validated.",
            }
        ],
        "decisions": [
            {
                "decision": "Use external LLM bridge output as the stage source of truth",
                "reason": "CLI coding-agent drivers are disabled for this runtime.",
            }
        ],
        "unresolved": [],
    }


def _normalize_stage_result(stage: str, payload: Any, changed: list[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        result = _default_stage_result(stage, changed)
    else:
        result = dict(payload)
        result["stage"] = stage
        result.setdefault("status", "completed")
        result.setdefault("summary", "Generated by the external LLM bridge.")
        result.setdefault("commands_run", [])
        result.setdefault("checks", [])
        result.setdefault("decisions", [])
        result.setdefault("unresolved", [])
    existing = [str(item) for item in result.get("files_changed") or []]
    result["files_changed"] = sorted({*existing, *changed})
    return result


def _build_bridge_prompt(
    prompt: str,
    *,
    layout: ProjectLayout,
    stage_name: str,
) -> tuple[str, tuple[str, ...]]:
    project = load_project(layout)
    pipeline = load_pipeline(str(project["pipeline"]))
    stage = pipeline.stage(stage_name)
    schemas = {
        artifact: load_artifact_schema(artifact)
        for artifact in stage.produces
    }
    contract = {
        "stage": stage.name,
        "required_artifacts": list(stage.produces),
        "artifact_schemas": schemas,
        "rules": [
            "Return ONLY one valid JSON object. No prose, no markdown code fences.",
            "The JSON object MUST have exactly these top-level keys: artifacts, stage_result, files.",
            "Do NOT nest those keys under any wrapper such as 'response', 'result', 'data', 'output', or 'required_response_shape'.",
            "artifacts must map each required artifact name to a JSON object that validates against its schema.",
            "You cannot execute commands or edit files directly; put any source files to create or replace under files (optional, may be {}).",
            "Do not put .appforge artifacts or .appforge/stage-result.json under files; they are written from artifacts and stage_result.",
            "Do not include secrets, API keys, tokens, or credentials.",
            "Use only relative file paths. Do not write .appforge, .git, parent-directory, or absolute paths.",
            "stage_result.status should be 'completed' unless there is a true blocker.",
        ],
        "response_shape": {
            "artifacts": {artifact: "<JSON object matching its schema>" for artifact in stage.produces},
            "stage_result": "<JSON object matching stage-result.schema.json>",
            "files": {"relative/path.ext": "UTF-8 text content to write, optional"},
        },
    }
    bridge_instruction = (
        "# External LLM bridge execution contract\n\n"
        "You are running through an API-only LLM bridge, not a local CLI agent. "
        "Produce a machine-readable JSON envelope that the AppForge runner will apply "
        "to the workspace. Respond with the envelope JSON object only.\n\n"
        "## Required response shape (return exactly this top-level structure)\n"
        "```json\n"
        f"{json.dumps(contract, ensure_ascii=False, indent=2)}\n"
        "```\n\n"
        "# Original OpenAppForge stage packet\n\n"
        f"{prompt}"
    )
    return bridge_instruction, stage.produces


_ENVELOPE_WRAPPER_KEYS = (
    "required_response_shape",
    "response_shape",
    "response",
    "result",
    "data",
    "output",
    "payload",
    "envelope",
    "body",
)


def _unwrap_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Descend through a single LLM-added wrapper key to the real envelope.

    External models sometimes echo the contract shape (wrapping the answer under
    ``required_response_shape`` or ``response``) instead of returning the flat
    envelope. If the top-level object does not itself look like an envelope,
    unwrap one wrapper level so artifact extraction stays robust.
    """
    if not envelope:
        return envelope
    if any(key in envelope for key in ("artifacts", "stage_result", "files")):
        return envelope
    wrappers = [key for key in _ENVELOPE_WRAPPER_KEYS if key in envelope]
    if len(wrappers) == 1:
        inner = envelope[wrappers[0]]
        if isinstance(inner, dict) and inner:
            return inner
    return envelope


def _locate_artifact_payload(envelope: dict[str, Any], artifact: str) -> dict[str, Any] | None:
    """Find an artifact object in the common envelope layouts."""
    artifacts = envelope.get("artifacts")
    if isinstance(artifacts, dict):
        candidate = artifacts.get(artifact)
        if isinstance(candidate, dict):
            return candidate
    direct = envelope.get(artifact)
    if isinstance(direct, dict):
        return direct
    return None


def _apply_bridge_envelope(
    layout: ProjectLayout,
    *,
    stage: str,
    produces: tuple[str, ...],
    response_text: str,
) -> list[str]:
    envelope = _unwrap_envelope(_extract_json_object(response_text))
    changed = _write_bridge_files(layout, envelope.get("files"))
    for artifact in produces:
        payload = _locate_artifact_payload(envelope, artifact)
        if payload is None and len(produces) == 1:
            # A model may return the artifact object directly without wrapping.
            payload = envelope
        if not isinstance(payload, dict):
            raise DriverError(
                f"LLM bridge response missing artifact object '{artifact}'. Return a flat "
                f"JSON object whose top-level keys are exactly: artifacts, stage_result, files."
            )
        try:
            validate_artifact(artifact, payload)
        except ArtifactValidationError as exc:
            raise DriverError(
                f"LLM bridge artifact '{artifact}' failed schema validation: {exc}. "
                f"Re-read the artifact schema and return a corrected JSON object."
            ) from exc
        artifact_path = layout.artifacts / f"{artifact}.json"
        atomic_write_json(artifact_path, payload)
        changed.append(str(artifact_path.relative_to(layout.root)))
    stage_result = _normalize_stage_result(stage, envelope.get("stage_result"), changed)
    atomic_write_json(layout.control / STAGE_RESULT_FILE_NAME, stage_result)
    return changed


class LLMBridgeDriver(AgentDriver):
    """Drive a stage with an external LLM through the local Node/Bun bridge.

    The bridge (``llm_bridge/``) reuses the coco ``@opencode-ai/llm`` engine to
    call any configured provider/model. This driver is single-shot per stage: it
    asks the model for a JSON envelope, then writes the requested files,
    artifacts, and stage completion record into the workspace.
    """

    name = "llm-bridge"

    def __init__(
        self,
        *,
        bridge_url: str,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        if not bridge_url:
            raise DriverError("LLM bridge driver requires APPFORGE_LLM_BRIDGE_URL")
        self.bridge_url = bridge_url
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens

    def run(
        self,
        prompt: str,
        *,
        layout: ProjectLayout,
        stage: str,
        attempt: int,
        timeout: int,
        cancel_event: threading.Event | None = None,
    ) -> DriverResult:
        from . import llm_bridge

        final_path = layout.logs / f"{stage}-attempt-{attempt}-llm-bridge-final.txt"
        started = time.monotonic()

        def duration() -> float:
            return round(time.monotonic() - started, 4)

        if cancel_event is not None and cancel_event.is_set():
            return DriverResult(
                success=False,
                exit_code=130,
                stdout="",
                stderr="Cancelled by user.",
                duration_seconds=duration(),
                command=["llm-bridge", "/generate"],
                final_message_path=str(final_path),
            )
        try:
            bridge_prompt, produces = _build_bridge_prompt(prompt, layout=layout, stage_name=stage)
        except Exception as exc:
            return DriverResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=f"Failed to prepare LLM bridge prompt: {exc}",
                duration_seconds=duration(),
                command=["llm-bridge", "/generate"],
                final_message_path=str(final_path),
            )
        try:
            result = llm_bridge.generate(
                self.bridge_url,
                prompt=bridge_prompt,
                system=(
                    "You are a precise software-production agent connected through an "
                    "API-only bridge. Return only the requested JSON envelope."
                ),
                provider=self.provider,
                model=self.model,
                max_tokens=self.max_tokens,
                timeout=max(timeout, 60),
            )
        except llm_bridge.BridgeError as exc:
            return DriverResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration(),
                command=["llm-bridge", "/generate"],
                final_message_path=str(final_path),
            )
        text = str(result.get("text") or "")
        atomic_write_text(final_path, _redact_text(text))
        if cancel_event is not None and cancel_event.is_set():
            return DriverResult(
                success=False,
                exit_code=130,
                stdout="",
                stderr="Cancelled by user.",
                duration_seconds=duration(),
                command=["llm-bridge", "/generate"],
                final_message_path=str(final_path),
            )
        try:
            changed = _apply_bridge_envelope(
                layout,
                stage=stage,
                produces=produces,
                response_text=text,
            )
        except DriverError as exc:
            return DriverResult(
                success=False,
                exit_code=2,
                stdout=_redact_text(text),
                stderr=str(exc),
                duration_seconds=duration(),
                command=["llm-bridge", "/generate"],
                final_message_path=str(final_path),
            )
        return DriverResult(
            success=True,
            exit_code=0,
            stdout=f"Applied LLM bridge envelope for {stage}: {', '.join(changed) or 'no files'}",
            stderr="",
            duration_seconds=duration(),
            command=["llm-bridge", "/generate"],
            final_message_path=str(final_path),
        )


def create_driver(
    name: str,
    *,
    unsafe: bool = False,
    model: str | None = None,
    max_turns: int | None = None,
    bridge_url: str | None = None,
    llm_provider: str | None = None,
) -> AgentDriver:
    normalized = name.casefold()
    if normalized == "auto":
        normalized = "llm-bridge"
    if normalized in {"codex", "claude"}:
        raise DriverError(
            "Codex and Claude CLI drivers were removed. Configure an external LLM "
            "provider in the local LLM bridge and use --driver llm-bridge."
        )
    if normalized in {"llm-bridge", "llm_bridge", "llm"}:
        return LLMBridgeDriver(
            bridge_url=bridge_url or DEFAULT_LLM_BRIDGE_URL,
            provider=llm_provider,
            model=model,
        )
    raise DriverError(f"Unknown driver {name!r}; use auto or llm-bridge")
