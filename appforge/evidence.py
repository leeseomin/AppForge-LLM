"""Host-owned validation records, outside generated-code workspace permissions."""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import platform
import shutil
import sys
import uuid
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from .models import PipelineSpec, ToolResult
from .snapshots import digest, source_manifest
from .util import atomic_write_json, read_json, redact, utc_now

POLICY_VERSION = "2.0"
_execution_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("appforge_validation", default={})


def control_store(workspace: Path) -> Path:
    workspace = workspace.resolve()
    base = workspace.parent / ".appforge-control"
    if base.is_symlink():
        raise ValueError("Host control store cannot be a symlink")
    base.mkdir(mode=0o700, exist_ok=True)
    root = base / hashlib.sha256(str(workspace).encode()).hexdigest()[:24]
    if root.is_symlink():
        raise ValueError("Host project store cannot be a symlink")
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def read_host(workspace: Path, name: str) -> dict[str, Any] | None:
    if Path(name).name != name or name.startswith("."):
        raise ValueError("Invalid host record name")
    path = control_store(workspace) / name
    if path.is_symlink():
        raise ValueError("Host record cannot be a symlink")
    data = read_json(path)
    return data if isinstance(data, dict) else None


def write_host(workspace: Path, name: str, payload: dict[str, Any]) -> None:
    if Path(name).name != name or name.startswith("."):
        raise ValueError("Invalid host record name")
    path = control_store(workspace) / name
    if path.is_symlink():
        raise ValueError("Host record cannot be a symlink")
    atomic_write_json(path, payload)


def policy_digest(pipeline: PipelineSpec) -> str:
    return digest({"policy_version": POLICY_VERSION, "pipeline": asdict(pipeline)})


def requirements_digest(workspace: Path) -> str:
    values = {}
    for name in ("product_brief", "requirements_spec", "engineering_spec", "change_plan"):
        path = workspace / ".appforge" / "artifacts" / f"{name}.json"
        if path.is_file():
            values[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest(values)


def artifact_digests(workspace: Path, names: tuple[str, ...]) -> dict[str, str | None]:
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            for name in names
            for path in [workspace / ".appforge" / "artifacts" / f"{name}.json"]}


def environment_digest(workspace: Path) -> str:
    """Hash installed dependencies, not merely their lockfile or modification time."""
    hasher = hashlib.sha256(json.dumps([platform.platform(), sys.version, sys.executable]).encode())
    from .tooling.sandbox import sanitized_path
    for name in ("node", "npm"):
        executable = shutil.which(name, path=sanitized_path(workspace))
        if executable:
            path = Path(executable).resolve()
            info = path.stat()
            hasher.update(name.encode())
            hasher.update(_runtime_digest(str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns).encode())
    for directory in ("node_modules", ".venv"):
        root = workspace / directory
        if not root.exists():
            continue
        for parent, directories, files in os.walk(root, followlinks=False):
            directories[:] = sorted(name for name in directories if name not in {"__pycache__", ".cache", ".vite"})
            for name in directories:
                path = Path(parent) / name
                if path.is_symlink():
                    hasher.update(path.relative_to(workspace).as_posix().encode())
                    hasher.update(os.readlink(path).encode())
            for name in sorted(files):
                path = Path(parent) / name
                hasher.update(path.relative_to(workspace).as_posix().encode())
                if path.is_symlink():
                    hasher.update(os.readlink(path).encode())
                else:
                    with path.open("rb") as handle:
                        for block in iter(lambda: handle.read(1024 * 1024), b""):
                            hasher.update(block)
    return hasher.hexdigest()


@lru_cache(maxsize=16)
def _runtime_digest(path: str, size: int, modified: int, changed: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextlib.contextmanager
def execution_context(**values: Any) -> Iterator[None]:
    token = _execution_context.set({**_execution_context.get(), **values})
    try:
        yield
    finally:
        _execution_context.reset(token)


def frozen_workspace(workspace: Path) -> bool:
    return _execution_context.get().get("frozen_workspace") == str(workspace.resolve())


def validation_epoch() -> str | None:
    return _execution_context.get().get("epoch")


def begin_attempt(attempt: int) -> None:
    """Rotate within the runner's scoped context; an outer repair is a new session."""
    _execution_context.set({**_execution_context.get(), "epoch": uuid.uuid4().hex, "attempt": attempt})


def context_value(name: str, default: Any = None) -> Any:
    return _execution_context.get().get(name, default)


def record_execution(workspace: Path, tool: str, before: str, result: ToolResult) -> str:
    context = _execution_context.get()
    owner = Path(context.get("owner") or workspace)
    requirements = context.get("requirements_digest") or requirements_digest(owner)
    if tool == "run_tests" and result.data.get("tests"):
        tests = result.data["tests"]
        inventory = read_host(owner, "test-inventory.json") or {}
        current_ids = set(tests.get("test_ids", []))
        prior_ids = set(inventory.get("test_ids", [])) if inventory.get("requirements_digest") == requirements else set()
        executed = set(tests.get("executed_test_ids", []))
        prior_executed = set(inventory.get("executed_test_ids", [])) if inventory.get("requirements_digest") == requirements else set()
        missing = (prior_ids - current_ids) | (prior_executed - executed)
        if missing and frozen_workspace(workspace):
            result.success = False
            result.error = "Previously collected tests disappeared; restore coverage before completing repair"
            result.data["code"] = "TEST_COVERAGE_REDUCED"
            result.data["missing_test_ids"] = sorted(missing)
        write_host(owner, "test-inventory.json", {"requirements_digest": requirements,
                   "test_ids": sorted(prior_ids | current_ids), "executed_test_ids": sorted(prior_executed | executed)})
    execution_id = "exec-" + uuid.uuid4().hex
    after = digest(source_manifest(workspace))
    record = {
        "schema_version": POLICY_VERSION,
        "execution_id": execution_id,
        "timestamp": utc_now(),
        "stage": context.get("stage"),
        "attempt": context.get("attempt"),
        "gate_id": tool,
        "input_snapshot_id": before,
        "output_snapshot_id": after,
        "requirements_digest": requirements,
        "policy_digest": context.get("policy_digest"),
        "test_origin": ("appforge_harness" if tool == "browser_acceptance" else
                        "host_static_analysis" if tool == "secret_scan" else "project_tests"),
        "status": "passed" if result.success and not result.data.get("skipped") and before == after else "failed",
        "result": json.loads(redact(json.dumps(result.to_dict(), ensure_ascii=False))),
    }
    record["result_digest"] = digest(record["result"])
    write_host(owner, f"{execution_id}.json", record)
    return execution_id
