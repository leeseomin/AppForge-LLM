from __future__ import annotations

from pathlib import Path
from typing import Any

from appforge.models import ToolResult

from ..base import Tool
from ..command import CommandPolicy, run_command
from ..detection import detect_stack


class DetectStackTool(Tool):
    name = "detect_stack"
    description = "Detect languages, frameworks, manifests, and package managers in the workspace."
    capability = "analysis"

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data=detect_stack(workspace))


class GitStatusTool(Tool):
    name = "git_status"
    description = "Show machine-readable Git working tree status."
    capability = "version_control"
    dependencies = ("cmd:git",)

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        result = run_command(workspace, ["git", "status", "--short", "--branch"], policy=CommandPolicy())
        if not result.success and "not a git repository" in (result.data.get("stderr") or "").lower():
            return ToolResult(success=True, data={"is_repository": False, "status": ""})
        result.data["is_repository"] = result.success
        result.data["status"] = result.data.get("stdout", "")
        return result


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Return the current Git diff without changing the repository."
    capability = "version_control"
    dependencies = ("cmd:git",)

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        staged = bool(inputs.get("staged", False))
        argv = ["git", "diff", "--no-ext-diff", "--stat"]
        if staged:
            argv.insert(2, "--cached")
        stat = run_command(workspace, argv, policy=CommandPolicy())
        full_argv = ["git", "diff", "--no-ext-diff", "--unified=3"]
        if staged:
            full_argv.insert(2, "--cached")
        full = run_command(workspace, full_argv, policy=CommandPolicy())
        if not full.success:
            return full
        return ToolResult(success=True, data={"stat": stat.data.get("stdout", ""), "diff": full.data.get("stdout", "")})


class GitInitTool(Tool):
    name = "git_init"
    description = "Initialize a Git repository in the workspace if one does not exist."
    capability = "version_control"
    dependencies = ("cmd:git",)
    destructive = True

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        if (workspace / ".git").exists():
            return ToolResult(success=True, data={"initialized": False, "reason": "already a repository"})
        result = run_command(workspace, ["git", "init"], policy=CommandPolicy(allow_destructive=True))
        result.data["initialized"] = result.success
        return result
