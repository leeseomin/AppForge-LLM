from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from appforge.constants import DEFAULT_COMMAND_TIMEOUT, MAX_CAPTURE_CHARS
from appforge.models import ToolResult
from appforge.util import redact, truncate

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

SAFE_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
}


@dataclass(frozen=True)
class CommandPolicy:
    allow_destructive: bool = False
    allow_network: bool = False
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT


def normalize_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        parts = shlex.split(command, posix=os.name != "nt")
    else:
        parts = [str(part) for part in command]
    if not parts:
        raise ValueError("Command cannot be empty")
    return parts


def validate_command(argv: list[str], policy: CommandPolicy) -> None:
    executable = Path(argv[0]).name.lower()
    rendered = shlex.join(argv)
    if not policy.allow_destructive:
        if executable in _SHELL_EXECUTABLES:
            raise PermissionError("Shell execution requires allow_destructive=true or sandbox isolation")
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
        ):
            raise PermissionError("Python package mutation requires allow_destructive=true or sandbox isolation")
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
            raise PermissionError("Network-capable package/repository operation requires allow_network=true")


def run_command(
    workspace: Path,
    command: str | Sequence[str],
    *,
    policy: CommandPolicy | None = None,
    env: dict[str, str] | None = None,
) -> ToolResult:
    policy = policy or CommandPolicy()
    argv = normalize_command(command)
    validate_command(argv, policy)
    merged_env = {
        key: value
        for key, value in os.environ.items()
        if key in SAFE_ENV_KEYS
    }
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
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
            data={"stdout": stdout, "stderr": stderr, "timed_out": True},
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
        },
        duration_seconds=round(time.monotonic() - started, 4),
        command=argv,
    )
