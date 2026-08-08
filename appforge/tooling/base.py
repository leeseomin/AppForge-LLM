from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

import jsonschema

from appforge.models import ToolResult
from appforge.util import command_exists, python_module_exists


class ToolStatus(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class Tool(ABC):
    """Contract for auditable workspace tools exposed to the agent."""

    name: ClassVar[str]
    description: ClassVar[str]
    capability: ClassVar[str]
    dependencies: ClassVar[tuple[str, ...]] = ()
    network_required: ClassVar[bool] = False
    destructive: ClassVar[bool] = False
    dependency_install_required: ClassVar[bool] = False
    # Safety flags this tool understands. The caller injects them from project
    # safety; they are never accepted from agent-supplied arguments.
    policy_inputs: ClassVar[tuple[str, ...]] = ()
    input_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    llm_exposed: ClassVar[bool] = False
    llm_description: ClassVar[str] = ""
    llm_parameters: ClassVar[dict[str, Any]] = {}

    def status(self) -> tuple[ToolStatus, list[str]]:
        missing: list[str] = []
        for dependency in self.dependencies:
            kind, _, value = dependency.partition(":")
            if kind == "cmd" and not command_exists(value):
                missing.append(dependency)
            elif kind == "python" and not python_module_exists(value):
                missing.append(dependency)
        return (ToolStatus.AVAILABLE if not missing else ToolStatus.UNAVAILABLE, missing)

    def info(self) -> dict[str, Any]:
        status, missing = self.status()
        return {
            "name": self.name,
            "description": self.description,
            "capability": self.capability,
            "status": status.value,
            "missing_dependencies": missing,
            "network_required": self.network_required,
            "destructive": self.destructive,
            "dependency_install_required": self.dependency_install_required,
            "policy_inputs": list(self.policy_inputs),
            "input_schema": self.input_schema,
            "llm_exposed": self.llm_exposed,
            "llm_description": self.llm_description or self.description,
            "llm_parameters": self.llm_parameters or self.input_schema,
        }

    def run(self, workspace: Path, inputs: dict[str, Any] | None = None) -> ToolResult:
        started = time.monotonic()
        workspace = workspace.expanduser().resolve()
        if not workspace.is_dir():
            return ToolResult(success=False, error=f"Workspace does not exist: {workspace}")
        status, missing = self.status()
        if status == ToolStatus.UNAVAILABLE:
            return ToolResult(success=False, error=f"Missing dependencies: {', '.join(missing)}")
        payload = inputs or {}
        try:
            jsonschema.validate(payload, self.input_schema)
        except jsonschema.ValidationError as exc:
            return ToolResult(success=False, error=f"Invalid tool input: {exc.message}")
        if self.destructive and not bool(payload.get("allow_destructive", False)):
            return ToolResult(success=False, error="This tool requires allow_destructive=true")
        if self.network_required and not bool(payload.get("allow_network", False)):
            return ToolResult(success=False, error="This tool requires allow_network=true")
        if self.dependency_install_required and not (
            bool(payload.get("allow_dependency_install", False))
            or bool(payload.get("allow_network", False))
        ):
            return ToolResult(
                success=False,
                error=(
                    "Dependency installation is disabled for this run. Do not retry it; "
                    "report the affected checks as unverified instead."
                ),
                data={"code": "DEPENDENCY_INSTALL_DISABLED", "retryable": False},
            )
        try:
            result = self.execute(workspace, payload)
        except Exception as exc:  # tool boundary: convert failures into structured results
            result = ToolResult(success=False, error=f"{type(exc).__name__}: {exc}")
        result.duration_seconds = round(time.monotonic() - started, 4)
        return result

    @abstractmethod
    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        raise NotImplementedError
