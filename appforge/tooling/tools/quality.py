from __future__ import annotations

from pathlib import Path
import json
import time
from typing import Any

from appforge.models import ToolResult

from ..base import Tool
from ..command import CommandPolicy, run_command
from ..detection import quality_commands
from ..command import normalize_command
from ..test_results import reporter_command, test_summary
from appforge.evidence import frozen_workspace, record_execution
from appforge.snapshots import digest, source_manifest


def _toolchain_missing_reason(workspace: Path, result: ToolResult) -> str | None:
    """Detect that the runner binary is absent rather than the check failing.

    Exit 127 (or a resolver error) means the command never ran, so reporting it as
    a failed quality gate would send the agent into an unfixable repair loop.
    """
    data = result.data or {}
    if data.get("timed_out"):
        return None
    returncode = data.get("returncode")
    if data.get("code") == "COMMAND_RUNNER_UNAVAILABLE" or returncode == 127:
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
        before = digest(source_manifest(workspace))
        command = normalize_command(command)
        reporter, report = "unsupported", None
        if self.command_key == "tests":
            command, reporter, report = reporter_command(workspace, command)
        started_ns = time.time_ns()
        result = run_command(
            workspace,
            command,
            env={"NODE_OPTIONS": f"--test-reporter=junit --test-reporter-destination={json.dumps(str(report))}"} if reporter == "node-junit" else None,
            policy=CommandPolicy(
                allow_network=bool(inputs.get("allow_network", False)),
                allow_destructive=False,
                allow_dependency_install=bool(inputs.get("allow_dependency_install", False)),
                timeout_seconds=int(inputs.get("timeout", 900)),
                read_only=frozen_workspace(workspace),
            ),
        )
        result.data["quality_kind"] = self.command_key
        missing_reason = None if result.success else _toolchain_missing_reason(workspace, result)
        if missing_reason:
            if result.data.get("code") != "COMMAND_RUNNER_UNAVAILABLE" and command[0] in {"npm", "pnpm", "yarn"}:
                result.data.update(code="PROJECT_COMMAND_UNAVAILABLE", reason="Check the package script and declare its runner, then use install_dependencies", skipped=False)
                result.data["execution_id"] = record_execution(workspace, self.name, before, result)
                return result
            result.success = True
            result.data["skipped"] = True
            result.data["reason"] = missing_reason
            result.data["code"] = "TOOLCHAIN_UNAVAILABLE"
            result.error = None
            result.data["execution_id"] = record_execution(workspace, self.name, before, result)
            return result
        result.data["skipped"] = False
        if self.command_key == "tests":
            try:
                summary = test_summary(reporter, report, str(result.data.get("stdout") or ""))
                result.data["tests"] = summary
                if summary["executed"] == 0 or summary["failed"]:
                    raise ValueError("No tests executed or test failures remain")
            except (ValueError, OSError) as exc:
                if result.success:
                    result.success = False
                    result.error = str(exc)
                    result.data["code"] = "TEST_EVIDENCE_MISSING"
        elif self.command_key == "build" and result.success and self._browser_build(workspace):
            outputs = [workspace / name / "index.html" for name in ("dist", "build", "out")]
            fresh = [path for path in outputs if path.is_file() and path.stat().st_mtime_ns >= started_ns]
            if not fresh:
                result.success = False
                result.error = "Production build produced no fresh browser entry point (dist, build, or out/index.html)"
                result.data["code"] = "BUILD_OUTPUT_MISSING"
            else:
                result.data["entrypoint"] = fresh[0].relative_to(workspace).as_posix()
        result.data["execution_id"] = record_execution(workspace, self.name, before, result)
        return result

    @staticmethod
    def _browser_build(workspace: Path) -> bool:
        from appforge.evidence import context_value
        from appforge.models import ProjectLayout
        from appforge.projects import load_project
        if context_value("profile") is not None:
            return context_value("profile") in {"javascript-web", "typescript-web"}
        if not (workspace / ".appforge" / "project.json").is_file():
            return False
        return load_project(ProjectLayout.from_root(workspace)).get("pipeline") in {"web-app", "web-app-lite"}


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
