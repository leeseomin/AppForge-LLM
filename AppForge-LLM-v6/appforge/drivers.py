from __future__ import annotations

import json
import re
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

import jsonschema

from .artifacts import ArtifactValidationError, load_artifact_schema, validate_artifact
from .constants import MAX_CAPTURE_CHARS, SCHEMAS_DIR, STAGE_RESULT_FILE_NAME
from .models import DriverResult, ProjectLayout, ToolResult
from .pipelines import load_pipeline
from .projects import load_project
from .util import atomic_write_json, atomic_write_text, read_json, redact, truncate

DEFAULT_LLM_BRIDGE_URL = "http://127.0.0.1:8788"
MAX_BRIDGE_FILES = 120
MAX_BRIDGE_FILE_BYTES = 512_000
MAX_BRIDGE_TOTAL_BYTES = 5_000_000


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
    payloads = _iter_file_payloads(files)
    if len(payloads) > MAX_BRIDGE_FILES:
        raise DriverError(f"Too many files from LLM bridge: {len(payloads)} > {MAX_BRIDGE_FILES}")

    changed: list[str] = []
    total_size = 0
    for relative_path, content in payloads:
        size = len(content.encode("utf-8"))
        if size > MAX_BRIDGE_FILE_BYTES:
            raise DriverError(
                f"File too large from LLM bridge: {relative_path} "
                f"({size} > {MAX_BRIDGE_FILE_BYTES} bytes)"
            )
        total_size += size
        if total_size > MAX_BRIDGE_TOTAL_BYTES:
            raise DriverError(
                f"Generated file payload is too large: {total_size} > "
                f"{MAX_BRIDGE_TOTAL_BYTES} bytes"
            )
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
                "passed": None,
                "verification": "unverified-self-report",
                "evidence": "Envelope parsed; artifact schemas validated. No behavioral verification performed.",
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


def _bridge_envelope_response_format(stage_name: str, produces: tuple[str, ...]) -> dict[str, Any]:
    """Best-effort structured-output hint for providers that support JSON schema.

    The runtime still keeps the robust scanner fallback because not every provider
    supports structured output, and artifact schemas are validated again after the
    response is applied.
    """
    return {
        "type": "json",
        "schema": {
            "type": "object",
            "required": ["artifacts", "stage_result", "files"],
            "properties": {
                "artifacts": {
                    "type": "object",
                    "required": list(produces),
                    "additionalProperties": True,
                },
                "stage_result": {"type": "object", "additionalProperties": True},
                "files": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "additionalProperties": True,
        },
    }


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
                response_format=_bridge_envelope_response_format(stage, produces),
                timeout=max(timeout, 60),
                cancel_event=cancel_event,
            )
        except llm_bridge.BridgeCancelled:
            return DriverResult(
                success=False,
                exit_code=130,
                stdout="",
                stderr="Cancelled by user.",
                duration_seconds=duration(),
                command=["llm-bridge", "/generate"],
                final_message_path=str(final_path),
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


def _stage_result_schema() -> dict[str, Any]:
    return read_json(SCHEMAS_DIR / "stage-result.schema.json", {}) or {}


def _relpath(path: Path, layout: ProjectLayout) -> str:
    try:
        return path.resolve().relative_to(layout.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _json_tool_result(result: ToolResult | dict[str, Any] | str, *, max_chars: int = MAX_CAPTURE_CHARS) -> str:
    if isinstance(result, ToolResult):
        payload: Any = result.to_dict()
    else:
        payload = result
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(payload)
    return redact(truncate(text, max_chars))


def _read_json_object_arg(value: Any, *, field: str = "payload") -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DriverError(f"{field} must be a JSON object or JSON-encoded object: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise DriverError(f"{field} must be a JSON object")


_INTERNAL_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "submit_artifact",
        "description": "Submit one AppForge artifact JSON object. The runner validates it immediately and writes it to .appforge/artifacts/<name>.json.",
        "parameters": {
            "type": "object",
            "required": ["name", "payload"],
            "properties": {
                "name": {"type": "string"},
                "payload": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_stage_result",
        "description": "Submit the final stage-result JSON object after required files/artifacts are complete.",
        "parameters": {
            "type": "object",
            "required": ["payload"],
            "properties": {
                "payload": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
]


class LLMBridgeAgentDriver(AgentDriver):
    """Multi-turn tool-use driver backed by the local LLM bridge.

    The bridge owns model/provider protocol details. The Python runner executes
    all workspace tools so path, command, network, and destructive-operation
    policy remains in the existing AppForge security boundary.
    """

    name = "llm-bridge-agent"
    AGENT_STAGES = {"implementation", "verification", "fix", "regression"}

    def __init__(
        self,
        *,
        bridge_url: str,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        max_turns: int | None = None,
    ) -> None:
        if not bridge_url:
            raise DriverError("LLM bridge agent driver requires APPFORGE_LLM_BRIDGE_URL")
        self.bridge_url = bridge_url
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens
        self.max_turns = int(max_turns or 40)
        self.max_usage_tokens = 240_000
        self.max_tool_seconds = 120
        self._single_shot = LLMBridgeDriver(
            bridge_url=bridge_url,
            provider=provider,
            model=model,
            max_tokens=max_tokens,
        )
        self._event_handler: Callable[[dict[str, Any]], None] | None = None

    def set_event_handler(self, handler: Callable[[dict[str, Any]], None] | None) -> None:
        self._event_handler = handler

    def _emit(self, payload: dict[str, Any]) -> None:
        if self._event_handler is None:
            return
        try:
            self._event_handler(payload)
        except Exception:
            # Event relay must never break stage execution.
            return

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
        if stage not in self.AGENT_STAGES:
            return self._single_shot.run(
                prompt,
                layout=layout,
                stage=stage,
                attempt=attempt,
                timeout=timeout,
                cancel_event=cancel_event,
            )

        from . import llm_bridge

        final_path = layout.logs / f"{stage}-attempt-{attempt}-llm-bridge-agent-final.txt"
        tool_log_path = layout.logs / f"{stage}-attempt-{attempt}-agent-tools.jsonl"
        started = time.monotonic()
        transcript: list[str] = []
        changed: set[str] = set()
        submitted_artifacts: set[str] = set()
        stage_result_submitted = False
        turns = 0
        repeated_tool_calls: dict[str, int] = {}
        usage_tokens = 0

        def duration() -> float:
            return round(time.monotonic() - started, 4)

        def tool_key(name: str, arguments: dict[str, Any]) -> str:
            try:
                args = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
            except TypeError:
                args = str(arguments)
            return f"{name}:{args}"

        def fail(message: str, *, code: int = 2, stdout: str = "") -> DriverResult:
            atomic_write_text(final_path, _redact_text("".join(transcript) or message))
            return DriverResult(
                success=False,
                exit_code=code,
                stdout=stdout,
                stderr=message,
                duration_seconds=duration(),
                command=["llm-bridge", "/agent"],
                final_message_path=str(final_path),
            )

        if cancel_event is not None and cancel_event.is_set():
            return fail("Cancelled by user.", code=130)

        try:
            project = load_project(layout)
            pipeline = load_pipeline(str(project["pipeline"]))
            stage_spec = pipeline.stage(stage)
            tools = self._build_agent_tools(stage_spec)
            system = (
                "You are an AppForge v6 software-production agent. Use tools to read, write, "
                "test, and repair the workspace. Never claim success until required artifacts "
                "and a stage result have been submitted through tools and validation succeeds."
            )
            guidance = self._agent_contract_prompt(prompt, stage_spec)
            generation: dict[str, Any] = {}
            if self.max_tokens is not None:
                generation["maxTokens"] = self.max_tokens
            active_prompt = guidance
            max_agent_sessions = 2
            for session_number in range(1, max_agent_sessions + 1):
                session_id: str | None = None
                try:
                    session = llm_bridge.agent_start(
                        self.bridge_url,
                        prompt=active_prompt,
                        system=system,
                        provider=self.provider,
                        model=self.model,
                        generation=generation or None,
                        tools=tools,
                        timeout=max(timeout, 60),
                        cancel_event=cancel_event,
                    )
                    session_id = str(session.get("session_id") or session.get("id") or "")
                    if not session_id:
                        return fail("LLM bridge agent did not return a session id.", code=1)
                    for event in llm_bridge.agent_events(
                        self.bridge_url,
                        session_id,
                        timeout=max(timeout, 60),
                        cancel_event=cancel_event,
                    ):
                        if cancel_event is not None and cancel_event.is_set():
                            return fail("Cancelled by user.", code=130)
                        etype = str(event.get("type") or event.get("event") or "")
                        if etype in {"text_delta", "text-delta"}:
                            text = str(event.get("text") or "")
                            transcript.append(text)
                            self._emit({"type": "llm_text", "stage": stage, "delta": text})
                            continue
                        if etype == "tool_call":
                            turns += 1
                            if turns > self.max_turns:
                                return fail(
                                    "AGENT_TURN_BUDGET_EXCEEDED: tool-call turn budget exceeded.",
                                    stdout=_redact_text("".join(transcript)),
                                )
                            call_id = str(event.get("call_id") or event.get("id") or "")
                            name = str(event.get("name") or "")
                            arguments = event.get("arguments")
                            if not isinstance(arguments, dict):
                                arguments = event.get("input") if isinstance(event.get("input"), dict) else {}
                            arguments_dict = dict(arguments or {})
                            self._emit({"type": "tool_call", "stage": stage, "name": name})
                            key = tool_key(name, arguments_dict)
                            repeated_tool_calls[key] = repeated_tool_calls.get(key, 0) + 1
                            if repeated_tool_calls[key] >= 3:
                                warning = ToolResult(
                                    success=False,
                                    error=(
                                        "Repeated identical tool call detected. Re-read the prior tool result, "
                                        "change the arguments or strategy, and avoid a loop."
                                    ),
                                    data={"code": "REPEATED_IDENTICAL_TOOL_CALL", "tool": name},
                                )
                                result_text = _json_tool_result(warning)
                                new_changed, artifacts, is_error = set(), set(), True
                                self._append_tool_log(
                                    tool_log_path,
                                    {
                                        "stage": stage,
                                        "tool": name,
                                        "arguments": arguments_dict,
                                        "result": warning.to_dict(),
                                        "is_error": True,
                                        "loop_guard": True,
                                    },
                                )
                            else:
                                result_text, new_changed, artifacts, is_error = self._execute_agent_tool(
                                    layout,
                                    stage=stage,
                                    name=name,
                                    arguments=arguments_dict,
                                    project=project,
                                    tool_log_path=tool_log_path,
                                )
                            changed.update(new_changed)
                            submitted_artifacts.update(artifacts)
                            if name == "submit_stage_result" and not is_error:
                                stage_result_submitted = True
                            llm_bridge.agent_tool_result(
                                self.bridge_url,
                                session_id,
                                call_id=call_id,
                                result=result_text,
                                is_error=is_error,
                                timeout=max(timeout, 60),
                                cancel_event=cancel_event,
                            )
                            continue
                        if etype == "done":
                            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                            for key in ("total_tokens", "totalTokens", "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"):
                                value = usage.get(key) if isinstance(usage, dict) else None
                                if isinstance(value, (int, float)):
                                    usage_tokens += int(value)
                            if usage_tokens > self.max_usage_tokens:
                                return fail(
                                    "AGENT_TOKEN_BUDGET_EXCEEDED: reported bridge usage exceeded the stage token budget.",
                                    stdout=_redact_text("".join(transcript)),
                                )
                            break
                        if etype == "error":
                            return fail(str(event.get("message") or event.get("error") or "LLM bridge agent error."), code=1)
                finally:
                    if session_id:
                        try:
                            llm_bridge.agent_stop(self.bridge_url, session_id, timeout=5.0)
                        except Exception:
                            pass
                missing = [
                    artifact for artifact in stage_spec.produces if artifact not in submitted_artifacts
                ]
                if not missing or session_number >= max_agent_sessions:
                    break
                transcript.append(
                    "\n\n[AppForge recovery] Previous bridge session ended without required tool submissions; starting a continuation session.\n"
                )
                active_prompt = self._agent_recovery_prompt(
                    original_prompt=prompt,
                    stage_spec=stage_spec,
                    missing_artifacts=missing,
                    changed=sorted(changed),
                    submitted_artifacts=sorted(submitted_artifacts),
                    transcript="".join(transcript),
                )
        except llm_bridge.BridgeCancelled:
            return fail("Cancelled by user.", code=130)
        except llm_bridge.BridgeError as exc:
            return fail(str(exc), code=1)
        except DriverError as exc:
            return fail(str(exc), code=2)
        except Exception as exc:
            return fail(f"LLM bridge agent failed: {type(exc).__name__}: {exc}", code=1)

        missing = [artifact for artifact in stage_spec.produces if artifact not in submitted_artifacts]
        if missing:
            return fail(
                "LLM bridge agent finished without submitting required artifacts: " + ", ".join(missing),
                stdout=_redact_text("".join(transcript)),
            )
        if not stage_result_submitted:
            stage_result = _default_stage_result(stage, sorted(changed))
            stage_result["commands_run"] = [
                {"command": "llm-bridge /agent", "result": f"Agent completed with {turns} tool calls."}
            ]
            atomic_write_json(layout.control / STAGE_RESULT_FILE_NAME, stage_result)
            stage_result_submitted = True

        atomic_write_text(final_path, _redact_text("".join(transcript)))
        return DriverResult(
            success=True,
            exit_code=0,
            stdout=f"LLM bridge agent completed {stage}: {len(changed)} workspace/control file(s) changed, {turns} tool call(s).",
            stderr="",
            duration_seconds=duration(),
            command=["llm-bridge", "/agent"],
            final_message_path=str(final_path),
        )

    def _agent_contract_prompt(self, prompt: str, stage_spec: Any) -> str:
        schemas = {artifact: load_artifact_schema(artifact) for artifact in stage_spec.produces}
        contract = {
            "stage": stage_spec.name,
            "required_artifacts": list(stage_spec.produces),
            "artifact_schemas": schemas,
            "required_finish": [
                "Use submit_artifact(name, payload) for every required artifact.",
                "Use submit_stage_result({payload}) after artifacts and file edits are complete.",
                "Use read_text/search_text before modifying existing files.",
                "Use run_tests/run_build/run_lint when available before reporting completion.",
            ],
        }
        return (
            "# AppForge v6 tool-use stage contract\n\n"
            "You are in multi-turn agent mode. Write source files through write_text; do not try to put all files in one answer. "
            "Tool validation errors are actionable feedback; fix them inside this same session.\n\n"
            f"```json\n{json.dumps(contract, ensure_ascii=False, indent=2)}\n```\n\n"
            "# Original stage packet\n\n"
            f"{prompt}"
        )

    def _agent_recovery_prompt(
        self,
        *,
        original_prompt: str,
        stage_spec: Any,
        missing_artifacts: list[str],
        changed: list[str],
        submitted_artifacts: list[str],
        transcript: str,
    ) -> str:
        return (
            "# AppForge continuation after incomplete tool session\n\n"
            "The previous bridge session ended without required tool submissions. "
            "Continue in the same workspace. Do not describe what you will do; immediately "
            "use tools to finish the stage.\n\n"
            "## Required now\n"
            f"- Missing required artifacts: {', '.join(missing_artifacts)}\n"
            "- Use write_text for source files that still need to be created or fixed.\n"
            "- Use submit_artifact(name, payload) for every missing artifact.\n"
            "- Use submit_stage_result({payload}) after artifacts and file edits are complete.\n"
            "- Do not end with prose only.\n\n"
            "## Current submitted state\n"
            f"- Files changed so far: {changed or ['<none>']}\n"
            f"- Artifacts submitted so far: {submitted_artifacts or ['<none>']}\n\n"
            "## Contract reminder\n"
            f"Required artifacts for stage `{stage_spec.name}`: {list(stage_spec.produces)}\n\n"
            "## Previous transcript tail\n"
            f"{truncate(transcript, 4_000)}\n\n"
            "# Original stage packet\n\n"
            f"{original_prompt}"
        )

    def _build_agent_tools(self, stage_spec: Any) -> list[dict[str, Any]]:
        from .tooling.registry import ToolRegistry

        registry = ToolRegistry()
        exposed: list[dict[str, Any]] = []
        wanted = set(stage_spec.tools or ()) | {"workspace_tree", "read_text", "search_text", "write_text"}
        for tool in registry.all():
            if not getattr(tool, "llm_exposed", False):
                continue
            if tool.name not in wanted:
                continue
            info = tool.info()
            exposed.append(
                {
                    "name": tool.name,
                    "description": str(info.get("llm_description") or info.get("description") or tool.name),
                    "parameters": info.get("llm_parameters") or info.get("input_schema") or {"type": "object"},
                }
            )
        names = {tool["name"] for tool in exposed}
        for internal in _INTERNAL_AGENT_TOOLS:
            if internal["name"] not in names:
                exposed.append(dict(internal))
        return exposed

    def _append_tool_log(self, tool_log_path: Path, payload: dict[str, Any]) -> None:
        tool_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with tool_log_path.open("a", encoding="utf-8") as handle:
            handle.write(redact(truncate(line, MAX_CAPTURE_CHARS)) + "\n")

    def _execute_agent_tool(
        self,
        layout: ProjectLayout,
        *,
        stage: str,
        name: str,
        arguments: dict[str, Any],
        project: dict[str, Any],
        tool_log_path: Path,
    ) -> tuple[str, set[str], set[str], bool]:
        started = time.monotonic()
        changed: set[str] = set()
        artifacts: set[str] = set()
        is_error = False
        try:
            if name == "submit_artifact":
                result = self._submit_artifact(layout, arguments)
                rel = str(result.data.get("path") or "")
                if rel:
                    changed.add(rel)
                artifact = str(result.data.get("artifact") or arguments.get("name") or "")
                if result.success and artifact:
                    artifacts.add(artifact)
            elif name == "submit_stage_result":
                result = self._submit_stage_result(layout, stage, arguments)
                if result.success:
                    changed.add(f".appforge/{STAGE_RESULT_FILE_NAME}")
            elif name in {"read_text", "write_text"}:
                result = self._execute_file_tool_safely(layout, name, arguments)
                rel = str(result.data.get("path") or "") if result.data else ""
                if result.success and name == "write_text" and rel:
                    changed.add(rel)
            else:
                result = self._execute_registered_tool(layout, name, arguments, project)
            if not result.success:
                is_error = True
        except Exception as exc:
            result = ToolResult(success=False, error=f"{type(exc).__name__}: {exc}")
            is_error = True
        result.duration_seconds = round(time.monotonic() - started, 4)
        self._append_tool_log(
            tool_log_path,
            {
                "stage": stage,
                "tool": name,
                "arguments": arguments,
                "result": result.to_dict(),
                "is_error": is_error,
            },
        )
        return _json_tool_result(result), changed, artifacts, is_error

    def _execute_file_tool_safely(self, layout: ProjectLayout, name: str, arguments: dict[str, Any]) -> ToolResult:
        rel = str(arguments.get("path") or "")
        if not rel:
            return ToolResult(success=False, error="path is required")
        path = _safe_workspace_path(layout, rel)
        if name == "read_text":
            if not path.is_file():
                return ToolResult(success=False, error=f"Not a file: {rel}")
            raw = path.read_bytes()
            if b"\x00" in raw[:8192]:
                return ToolResult(success=False, error="Binary files are not supported by read_text")
            text = raw.decode("utf-8", errors="replace")
            limit = max(100, min(int(arguments.get("max_chars", 40_000)), 120_000))
            return ToolResult(success=True, data={"path": _relpath(path, layout), "content": truncate(text, limit), "size": len(raw)})
        if name == "write_text":
            if path.exists() and not bool(arguments.get("overwrite", True)):
                return ToolResult(success=False, error=f"File exists and overwrite=false: {rel}")
            content = str(arguments.get("content", ""))
            atomic_write_text(path, content)
            return ToolResult(success=True, data={"path": _relpath(path, layout), "chars": len(content)})
        return ToolResult(success=False, error=f"Unsupported file tool: {name}")

    def _execute_registered_tool(
        self,
        layout: ProjectLayout,
        name: str,
        arguments: dict[str, Any],
        project: dict[str, Any],
    ) -> ToolResult:
        from .tooling.registry import ToolRegistry

        registry = ToolRegistry()
        tool = registry.get(name)
        if not getattr(tool, "llm_exposed", False):
            return ToolResult(success=False, error=f"Tool is not exposed to LLM agent: {name}")
        safety = project.get("safety") if isinstance(project.get("safety"), dict) else {}
        payload = dict(arguments)
        if getattr(tool, "network_required", False):
            payload["allow_network"] = bool(safety.get("allow_network", False))
        else:
            payload.pop("allow_network", None)
        if getattr(tool, "destructive", False):
            payload["allow_destructive"] = bool(safety.get("allow_destructive", False))
        else:
            payload.pop("allow_destructive", None)
        try:
            requested_timeout = int(payload.get("timeout", self.max_tool_seconds))
        except (TypeError, ValueError):
            requested_timeout = self.max_tool_seconds
        payload["timeout"] = min(requested_timeout, self.max_tool_seconds, 120)
        return tool.run(layout.root, payload)

    def _submit_artifact(self, layout: ProjectLayout, arguments: dict[str, Any]) -> ToolResult:
        name = str(arguments.get("name") or "")
        if not name:
            return ToolResult(success=False, error="artifact name is required")
        try:
            payload = _read_json_object_arg(arguments.get("payload"), field="payload")
            validate_artifact(name, payload)
        except (ArtifactValidationError, DriverError) as exc:
            return ToolResult(success=False, error=str(exc))
        path = layout.artifacts / f"{name}.json"
        atomic_write_json(path, payload)
        return ToolResult(success=True, data={"artifact": name, "path": _relpath(path, layout), "valid": True})

    def _submit_stage_result(self, layout: ProjectLayout, stage: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            payload = _read_json_object_arg(arguments.get("payload"), field="payload")
            payload = _normalize_stage_result(stage, payload, [])
            jsonschema.validate(payload, _stage_result_schema())
        except (DriverError, jsonschema.ValidationError) as exc:
            return ToolResult(success=False, error=f"stage_result schema validation failed: {getattr(exc, 'message', str(exc))}")
        path = layout.control / STAGE_RESULT_FILE_NAME
        atomic_write_json(path, payload)
        return ToolResult(success=True, data={"path": _relpath(path, layout), "valid": True})


def create_driver(
    name: str,
    *,
    unsafe: bool = False,
    model: str | None = None,
    max_turns: int | None = None,
    bridge_url: str | None = None,
    llm_provider: str | None = None,
) -> AgentDriver:
    normalized = name.casefold().strip()
    if normalized == "auto":
        normalized = "llm-bridge-agent"
    if normalized in {"codex", "claude"}:
        raise DriverError(
            "Codex and Claude CLI drivers were removed. Configure an external LLM "
            "provider in the local LLM bridge and use --driver llm-bridge or llm-bridge-agent."
        )
    if normalized in {"llm-bridge-agent", "llm_bridge_agent", "llm-agent", "agent"}:
        return LLMBridgeAgentDriver(
            bridge_url=bridge_url or DEFAULT_LLM_BRIDGE_URL,
            provider=llm_provider,
            model=model,
            max_turns=max_turns,
        )
    if normalized in {"llm-bridge", "llm_bridge", "llm"}:
        return LLMBridgeDriver(
            bridge_url=bridge_url or DEFAULT_LLM_BRIDGE_URL,
            provider=llm_provider,
            model=model,
        )
    raise DriverError(f"Unknown driver {name!r}; use auto, llm-bridge, or llm-bridge-agent")
