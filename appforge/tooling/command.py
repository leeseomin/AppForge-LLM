from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from appforge.constants import DEFAULT_COMMAND_TIMEOUT, MAX_CAPTURE_CHARS
from appforge.models import ToolResult
from appforge.util import redact, truncate

from .sandbox import ExecutionSandboxUnavailable, sandbox_invocation, sanitized_path

_BLOCKED_PATTERNS = [
    re.compile(r"(^|\s)sudo(\s|$)", re.I),
    re.compile(r"(^|\s)(shutdown|reboot|halt|poweroff)(\s|$)", re.I),
    re.compile(r"(^|\s)(mkfs|fdisk|parted)(\s|$)", re.I),
    re.compile(r"rm\s+-[^\n]*r[^\n]*f[^\n]*\s+/(?:\s|$)", re.I),
    re.compile(r"git\s+(reset\s+--hard|clean\s+-[^\n]*f|push[^\n]*--force)", re.I),
    re.compile(r"(?:curl|wget)[^\n|]*\|\s*(?:sh|bash|zsh)", re.I),
    re.compile(r"dd\s+[^\n]*of=/dev/", re.I),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:", re.I),
]
_SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}
_INLINE_CODE_FLAGS = {"-c", "-e"}
_INTERPRETERS = {"node", "ruby", "perl"}
_PYTHON_MODULE_INSTALLERS = {"pip", "pip3"}
# Executables whose whole purpose is removing or truncating data. They stay behind
# allow_destructive even when workspace command execution is otherwise permitted.
_DESTRUCTIVE_EXECUTABLES = {"rm", "rmdir", "shred", "truncate", "unlink"}
_PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun", "cargo", "go", "gradle", "mvn", "flutter", "dart"}
_DEPENDENCY_INSTALL_VERBS = {"install", "ci", "add", "fetch", "download", "get", "restore", "sync"}

_PROTECTED_ENV_KEYS = {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP"}
_SENSITIVE_ENV_NAME = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE)",
    re.I,
)
_VALID_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Test runners such as vitest and jest default to an interactive watch loop, which
# never exits and would hang a gate until its timeout. Every major runner treats
# these as the signal to run once and exit. Callers may still override them.
NON_INTERACTIVE_ENV = {
    "CI": "true",
    "NO_COLOR": "1",
    "npm_config_yes": "true",
    "npm_config_audit": "false",
    "npm_config_fund": "false",
    "npm_config_update_notifier": "false",
}


@dataclass(frozen=True)
class CommandPolicy:
    allow_destructive: bool = False
    allow_network: bool = False
    # Permits package-manager dependency resolution whose writes stay inside the
    # workspace (node_modules, a workspace .venv, cargo/go module caches).
    allow_dependency_install: bool = False
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT


def normalize_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        parts = shlex.split(command, posix=os.name != "nt")
    else:
        parts = [str(part) for part in command]
    if not parts:
        raise ValueError("Command cannot be empty")
    return parts


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _is_workspace_dependency_install(argv: list[str], workspace: Path | None) -> bool:
    """True when argv resolves dependencies into the workspace rather than the host.

    Node/Rust/Go package managers write into the project directory, so they are safe
    under allow_dependency_install. `pip install` mutates whichever interpreter runs
    it, so it only qualifies when that interpreter lives inside the workspace.
    """
    executable = Path(argv[0]).name.lower()
    verbs = {arg.lower() for arg in argv[1:4] if not arg.startswith("-")}
    is_python = executable == "python" or executable.startswith("python")
    if is_python:
        if not (len(argv) >= 4 and argv[1] == "-m" and argv[2].lower() in _PYTHON_MODULE_INSTALLERS):
            return False
        if argv[3].lower() != "install":
            return False
        return workspace is not None and _is_inside(Path(argv[0]), workspace)
    if executable in _PYTHON_MODULE_INSTALLERS:
        return False
    return executable in _PACKAGE_MANAGERS and bool(verbs & _DEPENDENCY_INSTALL_VERBS)


def validate_command(
    argv: list[str],
    policy: CommandPolicy,
    *,
    workspace: Path | None = None,
) -> None:
    executable = Path(argv[0]).name.lower()
    rendered = shlex.join(argv)
    dependency_install = _is_workspace_dependency_install(argv, workspace)
    if not policy.allow_destructive:
        if executable in _SHELL_EXECUTABLES:
            raise PermissionError("Shell execution requires allow_destructive=true or sandbox isolation")
        if executable in _DESTRUCTIVE_EXECUTABLES:
            raise PermissionError(
                f"{executable!r} deletes or truncates data and requires allow_destructive=true"
            )
        is_python = executable == "python" or executable.startswith("python")
        is_interpreter = is_python or executable in _INTERPRETERS
        if is_interpreter and any(arg in _INLINE_CODE_FLAGS for arg in argv[1:3]):
            raise PermissionError("Inline interpreter execution requires allow_destructive=true or sandbox isolation")
        if (
            is_python
            and len(argv) >= 4
            and argv[1] == "-m"
            and argv[2].lower() in _PYTHON_MODULE_INSTALLERS
            and argv[3].lower() in {"install", "uninstall"}
            and not (dependency_install and policy.allow_dependency_install)
        ):
            raise PermissionError(
                "Python package mutation outside the workspace requires allow_destructive=true; "
                "create a workspace .venv and install with it instead"
            )
        for pattern in _BLOCKED_PATTERNS:
            if pattern.search(rendered):
                raise PermissionError(f"Blocked potentially destructive command: {rendered}")
    if not policy.allow_network:
        network_commands = {
            "curl",
            "wget",
            "ssh",
            "scp",
            "rsync",
            "git",
            "npm",
            "pnpm",
            "yarn",
            "pip",
            "pip3",
            "cargo",
            "go",
            "gradle",
            "mvn",
            "docker",
        }
        # Git and package managers also have local-only operations. Block only obvious network verbs.
        if executable in {"curl", "wget", "ssh", "scp"}:
            raise PermissionError("Network access is disabled for this tool invocation")
        rendered_lower = rendered.lower()
        network_verbs = (
            " install",
            " add ",
            " fetch",
            " pull",
            " push",
            " clone",
            " publish",
            " audit",
            " download",
        )
        if executable in network_commands and any(verb in f" {rendered_lower}" for verb in network_verbs):
            # Dependency resolution is its own capability: it reaches a package
            # registry but writes only into the workspace, so it is allowed without
            # opening general network access.
            if not (dependency_install and policy.allow_dependency_install):
                raise PermissionError(
                    "Network-capable package/repository operation requires allow_network=true "
                    "(or allow_dependency_install=true for workspace dependency installation)"
                )


def run_command(
    workspace: Path,
    command: str | Sequence[str],
    *,
    policy: CommandPolicy | None = None,
    env: dict[str, str] | None = None,
) -> ToolResult:
    policy = policy or CommandPolicy()
    argv = normalize_command(command)
    validate_command(argv, policy, workspace=workspace)
    started = time.monotonic()
    workspace = workspace.resolve()
    with tempfile.TemporaryDirectory(prefix="appforge-command-") as temporary:
        sandbox_home = Path(temporary).resolve()
        (sandbox_home / "tmp").mkdir(mode=0o700)
        merged_env = {
            "PATH": sanitized_path(workspace),
            "HOME": str(sandbox_home),
            "USER": "appforge-sandbox",
            "LOGNAME": "appforge-sandbox",
            "TMPDIR": str(sandbox_home / "tmp"),
            "TEMP": str(sandbox_home / "tmp"),
            "TMP": str(sandbox_home / "tmp"),
            **NON_INTERACTIVE_ENV,
        }
        if env:
            for raw_key, raw_value in env.items():
                key = str(raw_key)
                if not _VALID_ENV_NAME.fullmatch(key):
                    raise PermissionError(f"Invalid environment variable name: {key!r}")
                if key in _PROTECTED_ENV_KEYS:
                    raise PermissionError(f"Project commands cannot override protected environment variable {key}")
                if _SENSITIVE_ENV_NAME.search(key):
                    raise PermissionError(f"Project commands cannot receive secret-like environment variable {key}")
                merged_env[key] = str(raw_value)
        try:
            invocation = sandbox_invocation(
                workspace,
                sandbox_home,
                argv,
                allow_network=(
                    policy.allow_network
                    or (
                        policy.allow_dependency_install
                        and _is_workspace_dependency_install(argv, workspace)
                    )
                ),
            )
        except ExecutionSandboxUnavailable as exc:
            return ToolResult(
                success=False,
                error="Secure project execution sandbox is unavailable",
                data={
                    "stdout": "",
                    "stderr": "",
                    "timed_out": False,
                    "code": "EXECUTION_SANDBOX_UNAVAILABLE",
                    "reason": str(exc),
                },
                duration_seconds=round(time.monotonic() - started, 4),
                command=argv,
            )
        try:
            completed = subprocess.run(
                invocation.argv,
                cwd=workspace,
                env=merged_env,
                text=True,
                capture_output=True,
                timeout=max(1, int(policy.timeout_seconds)),
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = redact(truncate(exc.stdout or "", MAX_CAPTURE_CHARS))
            stderr = redact(truncate(exc.stderr or "", MAX_CAPTURE_CHARS))
            return ToolResult(
                success=False,
                error=f"Command timed out after {policy.timeout_seconds}s",
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": True,
                    "sandbox": invocation.backend,
                },
                duration_seconds=round(time.monotonic() - started, 4),
                command=argv,
            )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error="Command runner is unavailable",
                data={
                    "returncode": 127,
                    "stdout": "",
                    "stderr": "command not found",
                    "timed_out": False,
                    "sandbox": invocation.backend,
                },
                duration_seconds=round(time.monotonic() - started, 4),
                command=argv,
            )
    stdout = redact(truncate(completed.stdout, MAX_CAPTURE_CHARS))
    stderr = redact(truncate(completed.stderr, MAX_CAPTURE_CHARS))
    return ToolResult(
        success=completed.returncode == 0,
        error=None if completed.returncode == 0 else f"Command exited with {completed.returncode}",
        data={
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
            "sandbox": invocation.backend,
        },
        duration_seconds=round(time.monotonic() - started, 4),
        command=argv,
    )
