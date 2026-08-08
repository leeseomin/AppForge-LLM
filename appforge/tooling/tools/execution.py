from __future__ import annotations

import os
import sys
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
    # Not blanket-destructive: run_command executes argv directly (no shell) inside the
    # workspace, and validate_command decides per-command whether allow_destructive,
    # allow_network, or allow_dependency_install is required.
    destructive = False
    policy_inputs = ("allow_network", "allow_destructive", "allow_dependency_install")
    input_schema = {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "timeout": {"type": "integer"},
            "allow_network": {"type": "boolean"},
            "allow_destructive": {"type": "boolean"},
            "allow_dependency_install": {"type": "boolean"},
            "env": {"type": "object"},
        },
    }
    llm_parameters = {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "timeout": {"type": "integer"},
            "env": {"type": "object"},
        },
    }

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        policy = CommandPolicy(
            allow_network=bool(inputs.get("allow_network", False)),
            allow_destructive=bool(inputs.get("allow_destructive", False)),
            allow_dependency_install=bool(inputs.get("allow_dependency_install", False)),
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
    description = "Install project dependencies into the workspace using the detected package manager."
    capability = "dependencies"
    llm_exposed = True
    llm_description = "Install detected project dependencies into the workspace before running tests or builds."
    dependency_install_required = True
    policy_inputs = ("allow_network", "allow_destructive", "allow_dependency_install")

    def _python_commands(self, workspace: Path) -> list[list[str]] | None:
        """Install Python dependencies into a workspace-local .venv.

        Installing with the host interpreter would mutate the environment running
        AppForge, so the workspace gets its own interpreter first.
        """
        if (workspace / "requirements.txt").exists():
            target = ["-r", "requirements.txt"]
        elif (workspace / "pyproject.toml").exists():
            target = ["-e", ".[dev]"]
        else:
            return None
        venv_dir = workspace / ".venv"
        venv_python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        commands: list[list[str]] = []
        if not venv_python.exists():
            commands.append([sys.executable or "python", "-m", "venv", ".venv"])
        commands.append([str(venv_python), "-m", "pip", "install", *target])
        return commands

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        detected = detect_stack(workspace)
        managers = detected.get("package_managers") or []
        commands: list[list[str]] | None = None
        if "pnpm" in managers:
            commands = [["pnpm", "install", "--frozen-lockfile"] if (workspace / "pnpm-lock.yaml").exists() else ["pnpm", "install"]]
        elif "yarn" in managers:
            commands = [["yarn", "install", "--immutable"] if (workspace / "yarn.lock").exists() else ["yarn", "install"]]
        elif "npm" in managers:
            commands = [["npm", "ci"] if (workspace / "package-lock.json").exists() else ["npm", "install"]]
        elif "cargo" in managers:
            commands = [["cargo", "fetch"]]
        elif "go" in managers:
            commands = [["go", "mod", "download"]]
        elif "maven" in managers:
            commands = [["mvn", "dependency:go-offline"]]
        elif "gradle" in managers:
            commands = [["./gradlew", "dependencies"] if (workspace / "gradlew").exists() else ["gradle", "dependencies"]]
        elif "flutter" in managers:
            commands = [["flutter", "pub", "get"]]
        elif "python" in detected.get("languages", []):
            commands = self._python_commands(workspace)
        if not commands:
            return ToolResult(success=False, error="Could not infer a dependency installation command", data={"stack": detected})
        policy = CommandPolicy(
            allow_network=bool(inputs.get("allow_network", False)),
            allow_destructive=bool(inputs.get("allow_destructive", False)),
            allow_dependency_install=bool(inputs.get("allow_dependency_install", False)),
            timeout_seconds=int(inputs.get("timeout", 1200)),
        )
        result = ToolResult(success=False, error="No dependency command executed")
        for command in commands:
            result = run_command(workspace, command, policy=policy)
            if not result.success:
                break
        result.data["commands_run"] = [" ".join(command) for command in commands]
        return result
