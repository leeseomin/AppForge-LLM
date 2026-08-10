from __future__ import annotations

import os
import platform
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

from .sandbox import (
    WINDOWS_SANDBOX_ERROR_PREFIX,
    ExecutionSandboxUnavailable,
    sandbox_invocation,
    sanitized_path,
)

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
_WINDOWS_EXECUTABLE_SUFFIXES = {".exe", ".cmd", ".bat", ".com"}
_SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}
_INTERPRETERS = {"node", "ruby", "perl"}
_PYTHON_MODULE_INSTALLERS = {"pip", "pip3"}
# Executables whose whole purpose is removing or truncating data. They stay behind
# allow_destructive even when workspace command execution is otherwise permitted.
_DESTRUCTIVE_EXECUTABLES = {
    "rm",
    "rmdir",
    "shred",
    "truncate",
    "unlink",
    "sudo",
    "doas",
    "runas",
    "gsudo",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "mkfs",
    "fdisk",
    "parted",
    "dd",
    # Windows command processors expose several destructive built-ins.  The direct
    # executable spellings are retained here as a second line of defence for tools
    # and aliases installed by third parties.
    "del",
    "erase",
    "rd",
    "diskpart",
    "format",
    "format-volume",
    "remove-item",
    "clear-content",
    "stop-computer",
    "restart-computer",
}
_PACKAGE_MANAGERS = {
    "npm",
    "npx",
    "pnpm",
    "yarn",
    "bun",
    "pip",
    "pip3",
    "cargo",
    "go",
    "gradle",
    "gradlew",
    "mvn",
    "mvnw",
    "flutter",
    "dart",
    "dotnet",
}
_DEPENDENCY_INSTALL_VERBS = {
    "install",
    "ci",
    "add",
    "fetch",
    "download",
    "get",
    "restore",
    "sync",
    "dependencies",
    "dependency:go-offline",
}

_PROTECTED_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "COMSPEC",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
}
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


def _sandbox_environment(workspace: Path, sandbox_home: Path) -> dict[str, str]:
    temporary = sandbox_home / "tmp"
    environment = {
        "PATH": sanitized_path(workspace),
        "HOME": str(sandbox_home),
        "USER": "appforge-sandbox",
        "LOGNAME": "appforge-sandbox",
        "TMPDIR": str(temporary),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "PYTHONNOUSERSITE": "1",
        **NON_INTERACTIVE_ENV,
    }
    if platform.system() != "Windows":
        return environment

    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    roaming = sandbox_home / "AppData" / "Roaming"
    local = sandbox_home / "AppData" / "Local"
    home_drive, home_path = os.path.splitdrive(str(sandbox_home))
    roaming.mkdir(parents=True, exist_ok=True)
    local.mkdir(parents=True, exist_ok=True)
    environment.update(
        {
            "USERPROFILE": str(sandbox_home),
            "APPDATA": str(roaming),
            "LOCALAPPDATA": str(local),
            "HOMEDRIVE": home_drive or sandbox_home.drive or os.environ.get("SystemDrive", "C:"),
            "HOMEPATH": home_path or "\\",
            "SYSTEMROOT": system_root,
            "WINDIR": system_root,
            "COMSPEC": os.path.join(system_root, "System32", "cmd.exe"),
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            "OS": "Windows_NT",
        }
    )
    for key in (
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "SYSTEMDRIVE",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
    ):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    return environment


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


def canonical_executable(value: str) -> str:
    """Return a platform-neutral executable name for policy decisions.

    Windows launchers commonly appear as ``cmd.exe``, ``npm.cmd`` or
    ``gradlew.bat``.  ``pathlib.Path`` on a non-Windows host does not treat a
    backslash as a separator, so policy tests and cross-platform planning must
    split both path syntaxes explicitly.
    """

    # Win32 normalizes trailing spaces and periods in ordinary file names. Strip
    # them before suffix handling so ``cmd.exe.`` cannot avoid the same policy as
    # ``cmd.exe``.
    name = (
        str(value)
        .replace("\\", "/")
        .rsplit("/", maxsplit=1)[-1]
        .strip()
        .rstrip(" .")
        .casefold()
    )
    while True:
        stem, suffix = os.path.splitext(name)
        if suffix.casefold() not in _WINDOWS_EXECUTABLE_SUFFIXES:
            return name
        name = stem.rstrip(" .").casefold()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _is_python_executable(executable: str) -> bool:
    return executable == "py" or executable.startswith(("python", "pypy"))


def _python_module_invocation(argv: list[str]) -> tuple[str, list[str]] | None:
    """Return ``(module, remaining_args)`` for a Python ``-m`` invocation."""

    for index, argument in enumerate(argv[1:], start=1):
        if argument == "--":
            return None
        if argument == "-m" and index + 1 < len(argv):
            return argv[index + 1].casefold(), argv[index + 2 :]
    return None


def _pip_operation(arguments: list[str]) -> str | None:
    """Find a pip operation even when global options precede the subcommand."""

    operations = {
        "install",
        "uninstall",
        "download",
        "wheel",
        "index",
        "list",
        "show",
        "check",
        "freeze",
        "inspect",
        "cache",
        "config",
        "debug",
        "help",
        "hash",
        "search",
    }
    for argument in arguments:
        lowered = argument.casefold()
        if lowered in operations:
            return lowered
    return None


def _pip_selects_another_interpreter(arguments: list[str]) -> bool:
    return any(
        argument.casefold() == "--python" or argument.casefold().startswith("--python=")
        for argument in arguments
    )


def _uses_inline_interpreter_code(argv: list[str], executable: str) -> bool:
    """Detect interpreter code flags only in the interpreter-option prefix.

    Arguments after a Python ``-m`` module or after a script name belong to that
    program.  Treating a later package-manager flag such as ``pip install -e`` as
    Python's own ``-e`` option would block legitimate dependency installation.
    """

    if not _is_python_executable(executable):
        inline_flags = {
            "node": {"-e", "--eval", "-p", "--print"},
            "ruby": {"-e"},
            "perl": {"-e", "-E"},
        }.get(executable, set())
        for argument in argv[1:]:
            if argument == "--":
                return False
            if (
                argument in inline_flags
                or argument.startswith("--eval=")
                or any(
                    argument.startswith(flag) and len(argument) > len(flag)
                    for flag in inline_flags
                    if flag.startswith("-") and not flag.startswith("--")
                )
            ):
                return True
        return False

    index = 1
    options_with_values = {"-W", "-X", "--check-hash-based-pycs"}
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            return False
        if argument == "-m":
            return False
        if argument == "-c" or (argument.startswith("-c") and len(argument) > 2):
            return True
        if not argument.startswith("-"):
            return False
        option = argument.split("=", maxsplit=1)[0]
        if option in options_with_values and "=" not in argument and index + 1 < len(argv):
            index += 2
        else:
            index += 1
    return False


def _first_subcommand(
    arguments: list[str],
    *,
    value_options: set[str] | None = None,
) -> str | None:
    """Find a subcommand while skipping known global options."""

    options_with_values = value_options or set()
    index = 0
    while index < len(arguments):
        raw = arguments[index]
        argument = raw.casefold()
        if argument == "--":
            return arguments[index + 1].casefold() if index + 1 < len(arguments) else None
        if argument.startswith("-"):
            option = argument.split("=", maxsplit=1)[0]
            index += 2 if option in options_with_values and "=" not in argument else 1
            continue
        return argument
    return None


def _package_manager_subcommand(executable: str, argv: list[str]) -> str | None:
    value_options: dict[str, set[str]] = {
        "npm": {
            "--prefix",
            "--workspace",
            "-w",
            "--registry",
            "--cache",
            "--userconfig",
        },
        "pnpm": {"--dir", "--prefix", "-c", "--workspace-root", "--filter"},
        "yarn": {"--cwd", "--cache-folder"},
        "bun": {"--cwd"},
        "cargo": {"--manifest-path", "--target-dir", "--config"},
        "gradle": {"--project-dir", "-p", "--gradle-user-home", "-g"},
        "gradlew": {"--project-dir", "-p", "--gradle-user-home", "-g"},
        "mvn": {"-f", "--file", "-s", "--settings", "-gs", "--global-settings"},
        "mvnw": {"-f", "--file", "-s", "--settings", "-gs", "--global-settings"},
    }
    return _first_subcommand(argv[1:], value_options=value_options.get(executable, set()))


def _is_workspace_dependency_install(argv: list[str], workspace: Path | None) -> bool:
    """True when argv resolves dependencies into the workspace rather than the host.

    Node/Rust/Go package managers write into the project directory, so they are safe
    under allow_dependency_install. `pip install` mutates whichever interpreter runs
    it, so it only qualifies when that interpreter lives inside the workspace.
    """
    executable = canonical_executable(argv[0])
    verbs = {arg.casefold() for arg in argv[1:5] if not arg.startswith("-")}
    if _is_python_executable(executable):
        module_invocation = _python_module_invocation(argv)
        if not module_invocation:
            return False
        module, module_args = module_invocation
        if canonical_executable(module) not in _PYTHON_MODULE_INSTALLERS:
            return False
        if _pip_operation(module_args) != "install" or _pip_selects_another_interpreter(module_args):
            return False
        if workspace is None:
            return False
        raw_interpreter = str(argv[0])
        interpreter = Path(raw_interpreter)
        if not interpreter.is_absolute() and not any(
            separator in raw_interpreter for separator in ("/", "\\")
        ):
            # A bare ``python``/``python.exe`` name is resolved through PATH; it is
            # not evidence that the interpreter belongs to the project venv.
            return False
        if not interpreter.is_absolute():
            interpreter = workspace / interpreter
        return _is_inside(interpreter, workspace)
    if executable in _PYTHON_MODULE_INSTALLERS:
        return False
    if executable not in _PACKAGE_MANAGERS:
        return False

    subcommand = _package_manager_subcommand(executable, argv)
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        return subcommand in {"install", "ci", "add", "fetch", "update"}
    if executable == "cargo":
        return subcommand == "fetch"
    if executable == "go":
        return [argument.casefold() for argument in argv[1:3]] == ["mod", "download"]
    if executable in {"gradle", "gradlew"}:
        return subcommand in {"dependencies", "builddependencies"}
    if executable in {"mvn", "mvnw"}:
        return subcommand in {"dependency:go-offline", "dependency:resolve"}
    if executable in {"flutter", "dart"}:
        return [argument.casefold() for argument in argv[1:3]] == ["pub", "get"]
    if executable == "dotnet":
        return subcommand == "restore"
    return bool(verbs & _DEPENDENCY_INSTALL_VERBS)


def _git_subcommand(argv: list[str]) -> tuple[str | None, list[str]]:
    """Return the Git subcommand after skipping common global options."""

    arguments = argv[1:]
    options_with_values = {
        "-c",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--exec-path",
        "--config-env",
        "--super-prefix",
    }
    index = 0
    while index < len(arguments):
        raw = arguments[index]
        argument = raw.casefold()
        if argument == "--":
            index += 1
            break
        if argument.startswith("-"):
            option = argument.split("=", maxsplit=1)[0]
            index += 2 if option in options_with_values and "=" not in argument else 1
            continue
        return argument, arguments[index + 1 :]
    if index < len(arguments):
        return arguments[index].casefold(), arguments[index + 1 :]
    return None, []


def _is_destructive_git_command(argv: list[str]) -> bool:
    """Detect destructive Git operations after Windows alias canonicalization."""

    if not argv or canonical_executable(argv[0]) != "git":
        return False
    command, raw_arguments = _git_subcommand(argv)
    arguments = [str(argument).casefold() for argument in raw_arguments]
    if command == "reset" and "--hard" in arguments:
        return True
    if command == "clean" and any(
        argument.startswith("--force")
        or (argument.startswith("-") and not argument.startswith("--") and "f" in argument[1:])
        for argument in arguments
    ):
        return True
    if command == "push" and any(
        argument == "-f"
        or argument == "--force"
        or argument.startswith("--force-with-lease")
        or argument.startswith("--force-if-includes")
        for argument in arguments
    ):
        return True
    return False


def _command_requires_network(
    argv: list[str],
    executable: str,
    dependency_install: bool,
) -> bool:
    if dependency_install:
        return True
    if executable in {"curl", "wget", "ssh", "scp"}:
        return True
    if executable == "rsync":
        return any(
            ":" in argument or argument.startswith(("rsync://", "ssh://"))
            for argument in argv[1:]
        )
    if executable == "git":
        subcommand, remaining = _git_subcommand(argv)
        if subcommand in {"clone", "fetch", "pull", "push", "ls-remote", "lfs"}:
            return True
        if subcommand == "remote" and any(
            argument.casefold() in {"update", "get-url"} for argument in remaining
        ):
            return True
        if subcommand == "submodule" and any(
            argument.casefold() in {"update", "sync"} for argument in remaining
        ):
            return True
        if subcommand == "archive" and any(
            argument.casefold().startswith("--remote") for argument in remaining
        ):
            return True
        return False
    if executable == "npx":
        lowered = {argument.casefold() for argument in argv[1:]}
        return not ({"--no-install", "--offline"} & lowered)
    if executable == "docker":
        subcommand = _first_subcommand(
            argv[1:],
            value_options={"--config", "--context", "--host", "-h", "--log-level"},
        )
        return subcommand in {"pull", "push", "login", "logout", "search", "buildx"}
    if executable in _PACKAGE_MANAGERS or executable in _PYTHON_MODULE_INSTALLERS:
        network_words = {
            "install",
            "ci",
            "add",
            "fetch",
            "download",
            "get",
            "restore",
            "sync",
            "update",
            "upgrade",
            "publish",
            "audit",
            "search",
            "view",
            "info",
            "show",
            "ping",
            "login",
            "logout",
            "whoami",
            "index",
            "wheel",
            "dlx",
            "x",
            "dependency:go-offline",
            "dependency:resolve",
        }
        return any(argument.casefold() in network_words for argument in argv[1:])
    if _is_python_executable(executable):
        module_invocation = _python_module_invocation(argv)
        if module_invocation and canonical_executable(module_invocation[0]) in (
            _PYTHON_MODULE_INSTALLERS
        ):
            return _pip_operation(module_invocation[1]) in {
                "install",
                "download",
                "wheel",
                "index",
                "search",
            }
    return False


def validate_command(
    argv: list[str],
    policy: CommandPolicy,
    *,
    workspace: Path | None = None,
) -> None:
    executable = canonical_executable(argv[0])
    rendered = shlex.join(argv)
    dependency_install = _is_workspace_dependency_install(argv, workspace)
    if dependency_install and not policy.allow_dependency_install:
        raise PermissionError(
            "Dependency installation requires allow_dependency_install=true"
        )
    if not policy.allow_destructive:
        if executable in _SHELL_EXECUTABLES:
            raise PermissionError("Shell execution requires allow_destructive=true or sandbox isolation")
        if executable in _DESTRUCTIVE_EXECUTABLES:
            raise PermissionError(
                f"{executable!r} deletes or truncates data and requires allow_destructive=true"
            )
        if _is_destructive_git_command(argv):
            raise PermissionError(f"Blocked potentially destructive Git command: {rendered}")
        is_python = _is_python_executable(executable)
        is_interpreter = is_python or executable in _INTERPRETERS
        if is_interpreter and _uses_inline_interpreter_code(argv, executable):
            raise PermissionError(
                "Inline interpreter execution requires allow_destructive=true or sandbox isolation"
            )
        module_invocation = _python_module_invocation(argv) if is_python else None
        direct_pip_mutation = bool(
            executable in _PYTHON_MODULE_INSTALLERS
            and _pip_operation(argv[1:]) in {"install", "uninstall"}
        )
        python_package_mutation = bool(
            module_invocation
            and canonical_executable(module_invocation[0]) in _PYTHON_MODULE_INSTALLERS
            and _pip_operation(module_invocation[1]) in {"install", "uninstall"}
        )
        if direct_pip_mutation or (
            python_package_mutation
            and not (dependency_install and policy.allow_dependency_install)
        ):
            raise PermissionError(
                "Python package mutation outside the workspace requires allow_destructive=true; "
                "create a workspace .venv and install with it instead"
            )
        for pattern in _BLOCKED_PATTERNS:
            if pattern.search(rendered):
                raise PermissionError(f"Blocked potentially destructive command: {rendered}")
    if not policy.allow_network and _command_requires_network(
        argv,
        executable,
        dependency_install,
    ):
        # Dependency resolution is its own capability: it reaches a package
        # registry but is permitted only when the dedicated project policy allows it.
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
        merged_env = _sandbox_environment(workspace, sandbox_home)
        if env:
            for raw_key, raw_value in env.items():
                key = str(raw_key)
                if not _VALID_ENV_NAME.fullmatch(key):
                    raise PermissionError(f"Invalid environment variable name: {key!r}")
                if key.upper() in _PROTECTED_ENV_KEYS:
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
    if (
        invocation.backend == "windows-appcontainer-job"
        and completed.returncode == 126
        and completed.stderr.startswith(WINDOWS_SANDBOX_ERROR_PREFIX)
    ):
        reason = completed.stderr.removeprefix(WINDOWS_SANDBOX_ERROR_PREFIX).strip()
        return ToolResult(
            success=False,
            error="Secure project execution sandbox is unavailable",
            data={
                "returncode": completed.returncode,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "sandbox": invocation.backend,
                "code": "EXECUTION_SANDBOX_UNAVAILABLE",
                "reason": redact(truncate(reason, MAX_CAPTURE_CHARS)),
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
