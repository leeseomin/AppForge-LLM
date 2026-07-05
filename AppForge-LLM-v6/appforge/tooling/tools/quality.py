from __future__ import annotations

from pathlib import Path
from typing import Any

from appforge.models import ToolResult

from ..base import Tool
from ..command import CommandPolicy, run_command
from ..detection import quality_commands


class _QualityTool(Tool):
    command_key: str

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
                timeout_seconds=int(inputs.get("timeout", 900)),
            ),
        )
        result.data["skipped"] = False
        result.data["quality_kind"] = self.command_key
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
