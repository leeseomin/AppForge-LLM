from __future__ import annotations

from pathlib import Path
from typing import Any

from appforge.models import ToolResult

from ..base import Tool
from ..command import CommandPolicy, run_command
from ..detection import quality_commands


_TOOLCHAIN_MISSING_MARKERS = (
    "command not found",
    "not found: ",
    "no such file or directory",
    "is not recognized as an internal or external command",
    "cannot find module",
    "module not found",
    "err_module_not_found",
    "npm error enoent",
)


def _toolchain_missing_reason(workspace: Path, result: ToolResult) -> str | None:
    """Detect that the runner binary is absent rather than the check failing.

    Exit 127 (or a resolver error) means the command never ran, so reporting it as
    a failed quality gate would send the agent into an unfixable repair loop.
    """
    data = result.data or {}
    if data.get("timed_out"):
        return None
    returncode = data.get("returncode")
    combined = f"{data.get('stdout') or ''}\n{data.get('stderr') or ''}".casefold()
    if returncode == 127 or any(marker in combined for marker in _TOOLCHAIN_MISSING_MARKERS):
        if (workspace / "package.json").exists() and not (workspace / "node_modules").exists():
            return "Node dependencies are not installed; run install_dependencies before this check."
        return "The command runner is not installed in this environment, so the check never executed."
    return None


class _QualityTool(Tool):
    command_key: str
    policy_inputs = ("allow_network", "allow_dependency_install")

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        commands = quality_commands(workspace)
        command = inputs.get("command") or commands.get(self.command_key)
        if not command:
            return ToolResult(
                success=True,
                data={"skipped": True, "reason": f"No {self.command_key} command detected", "detected_commands": commands},
            )
        result = run_command(
            workspace,
            command,
            policy=CommandPolicy(
                allow_network=bool(inputs.get("allow_network", False)),
                allow_destructive=False,
                allow_dependency_install=bool(inputs.get("allow_dependency_install", False)),
                timeout_seconds=int(inputs.get("timeout", 900)),
            ),
        )
        result.data["quality_kind"] = self.command_key
        missing_reason = None if result.success else _toolchain_missing_reason(workspace, result)
        if missing_reason:
            result.success = True
            result.data["skipped"] = True
            result.data["reason"] = missing_reason
            result.data["code"] = "TOOLCHAIN_UNAVAILABLE"
            result.error = None
            return result
        result.data["skipped"] = False
        return result


class RunTestsTool(_QualityTool):
    name = "run_tests"
    description = "Run the repository's detected test command."
    capability = "quality"
    llm_exposed = True
    llm_description = "Run detected or supplied automated tests and return stdout/stderr evidence."
    command_key = "tests"


class RunLintTool(_QualityTool):
    name = "run_lint"
    description = "Run the repository's detected lint command."
    capability = "quality"
    llm_exposed = True
    llm_description = "Run detected or supplied lint checks and return evidence."
    command_key = "lint"


class RunTypecheckTool(_QualityTool):
    name = "run_typecheck"
    description = "Run the repository's detected static type-check command."
    capability = "quality"
    llm_exposed = True
    llm_description = "Run detected or supplied static type checks and return evidence."
    command_key = "typecheck"


class RunBuildTool(_QualityTool):
    name = "run_build"
    description = "Run the repository's detected production build command."
    capability = "quality"
    llm_exposed = True
    llm_description = "Run detected or supplied production build command and return evidence."
    command_key = "build"


class CheckFormatTool(_QualityTool):
    name = "check_format"
    description = "Run a non-mutating format check when available."
    capability = "quality"
    llm_exposed = True
    llm_description = "Run a non-mutating formatting check when available."
    command_key = "format"
