from __future__ import annotations

from pathlib import Path
from typing import Any

from appforge.models import ToolResult

from ..base import Tool
from ..command import CommandPolicy, run_command
from ..detection import detect_stack, quality_commands


class RunCommandTool(Tool):
    name = "run_command"
    description = "Run one non-shell command in the workspace with safety and timeout controls."
    capability = "execution"
    llm_exposed = True
    llm_description = "Run a single whitelisted command in the workspace when tests or diagnostics require it."
    destructive = True
    input_schema = {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "timeout": {"type": "integer"},
            "allow_network": {"type": "boolean"},
            "allow_destructive": {"type": "boolean"},
            "env": {"type": "object"},
        },
    }

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        policy = CommandPolicy(
            allow_network=bool(inputs.get("allow_network", False)),
            allow_destructive=bool(inputs.get("allow_destructive", False)),
            timeout_seconds=int(inputs.get("timeout", 900)),
        )
        return run_command(workspace, inputs["command"], policy=policy, env=inputs.get("env"))


class DetectQualityCommandsTool(Tool):
    name = "detect_quality_commands"
    description = "Infer test, lint, type-check, format, and build commands from repository files."
    capability = "analysis"
    llm_exposed = True
    llm_description = "Infer test, lint, type-check, format, and build commands from repository files."

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data={"stack": detect_stack(workspace), "commands": quality_commands(workspace)})


class InstallDependenciesTool(Tool):
    name = "install_dependencies"
    description = "Install project dependencies using the detected package manager. Network is opt-in."
    capability = "dependencies"
    llm_exposed = True
    llm_description = "Install detected project dependencies when network access is explicitly enabled."
    network_required = True
    destructive = True

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        detected = detect_stack(workspace)
        managers = detected.get("package_managers") or []
        command: list[str] | None = None
        if "pnpm" in managers:
            command = ["pnpm", "install", "--frozen-lockfile"] if (workspace / "pnpm-lock.yaml").exists() else ["pnpm", "install"]
        elif "yarn" in managers:
            command = ["yarn", "install", "--immutable"] if (workspace / "yarn.lock").exists() else ["yarn", "install"]
        elif "npm" in managers:
            command = ["npm", "ci"] if (workspace / "package-lock.json").exists() else ["npm", "install"]
        elif "cargo" in managers:
            command = ["cargo", "fetch"]
        elif "go" in managers:
            command = ["go", "mod", "download"]
        elif "maven" in managers:
            command = ["mvn", "dependency:go-offline"]
        elif "gradle" in managers:
            command = ["./gradlew", "dependencies"] if (workspace / "gradlew").exists() else ["gradle", "dependencies"]
        elif "flutter" in managers:
            command = ["flutter", "pub", "get"]
        elif "python" in detected.get("languages", []):
            if (workspace / "requirements.txt").exists():
                command = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
            elif (workspace / "pyproject.toml").exists():
                command = ["python", "-m", "pip", "install", "-e", ".[dev]"]
        if command is None:
            return ToolResult(success=False, error="Could not infer a dependency installation command", data={"stack": detected})
        return run_command(
            workspace,
            command,
            policy=CommandPolicy(allow_network=bool(inputs.get("allow_network", False)), allow_destructive=True, timeout_seconds=int(inputs.get("timeout", 1200))),
        )
