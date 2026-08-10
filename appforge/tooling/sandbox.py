from __future__ import annotations

import os
import ntpath
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


class ExecutionSandboxUnavailable(RuntimeError):
    """Raised when untrusted project code cannot be confined safely."""


WINDOWS_SANDBOX_ERROR_PREFIX = "APPFORGE_WINDOWS_SANDBOX_UNAVAILABLE:"


@dataclass(frozen=True)
class SandboxInvocation:
    argv: list[str]
    backend: str


_MACOS_PROFILE_BASE = r"""
(version 1)
(allow default)

; Generated code may only write its workspace and disposable home.
(deny file-write* (subpath "/"))
(allow file-write*
  (subpath (param "WORKSPACE"))
  (subpath (param "SANDBOX_HOME"))
  (literal "/dev/null")
  (literal "/dev/tty"))

; Personal files, mounted user data and host temp files can contain credentials.
(deny file-read*
  (subpath "/Users")
  (subpath "/Volumes")
  (subpath "/private/var/folders")
  (subpath "/private/tmp")
  (subpath "/tmp"))
(allow file-read-metadata)
(allow file-read*
  (subpath (param "WORKSPACE"))
  (subpath (param "SANDBOX_HOME"))
  (subpath (param "TOOLCHAIN")))

; Do not let generated code ask trusted user services to read secrets on its behalf.
(deny process-info*)
(allow process-info* (target self))
(deny appleevent-send)
(deny mach-lookup
  (global-name-regex #"^com\\.apple\\.(securityd|secd)(\\.|$)")
  (global-name-regex #"^com\\.apple\\.(pboard|pasteboard)(\\.|$)")
  (global-name-regex #".*(launchservices|\\.lsd)(\\.|$)"))
(deny signal)
(allow signal (target self))

; Network is denied unless the caller adds the remote-only clause below.
(deny network*)
"""

_MACOS_REMOTE_NETWORK = r"""
(allow network-outbound (literal "/private/var/run/mDNSResponder"))
(allow network-outbound
  (require-all
    (remote ip)
    (require-not (remote ip "localhost:*"))))
(deny network-outbound
  (remote ip "localhost:*"))
"""


def _macos_invocation(
    workspace: Path,
    sandbox_home: Path,
    argv: list[str],
    *,
    allow_network: bool,
) -> SandboxInvocation:
    executable = Path("/usr/bin/sandbox-exec")
    if not executable.is_file():
        raise ExecutionSandboxUnavailable("macOS sandbox-exec is unavailable")
    profile = _MACOS_PROFILE_BASE + (_MACOS_REMOTE_NETWORK if allow_network else "")
    toolchain = _trusted_toolchain_root(workspace, argv[0])
    return SandboxInvocation(
        argv=[
            str(executable),
            "-D",
            f"WORKSPACE={workspace}",
            "-D",
            f"SANDBOX_HOME={sandbox_home}",
            "-D",
            f"TOOLCHAIN={toolchain}",
            "-p",
            profile,
            *argv,
        ],
        backend="macos-sandbox-exec",
    )


def _existing(paths: list[str]) -> list[Path]:
    return [path for raw in paths if (path := Path(raw)).exists()]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _lexically_inside(path: Path, root: Path) -> bool:
    try:
        path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    return True


def _trusted_toolchain_root(workspace: Path, executable: str) -> Path:
    if os.sep in executable:
        lexical = Path(executable)
        if not lexical.is_absolute():
            lexical = workspace / lexical
    else:
        resolved = shutil.which(executable, path=sanitized_path(workspace))
        if not resolved:
            # Let the sandboxed exec report the ordinary command-not-found error.
            return Path("/usr")
        lexical = Path(resolved)
    if _lexically_inside(lexical, workspace) and _inside(lexical, workspace):
        return workspace
    python_prefix = Path(sys.prefix)
    if _lexically_inside(lexical, python_prefix):
        return python_prefix
    allowed_system_roots = [
        Path("/System"),
        Path("/Library"),
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/opt"),
    ]
    if any(_inside(lexical, root) for root in allowed_system_roots):
        return lexical.parent
    raise ExecutionSandboxUnavailable("command executable is outside the workspace and trusted toolchains")


def _linux_invocation(
    workspace: Path,
    sandbox_home: Path,
    argv: list[str],
    *,
    allow_network: bool,
) -> SandboxInvocation:
    executable = shutil.which("bwrap")
    if not executable:
        raise ExecutionSandboxUnavailable("bubblewrap is required for Linux project execution")
    if allow_network:
        # Sharing the host network would make loopback services reachable. A
        # user-mode egress proxy can be added later without weakening this default.
        raise ExecutionSandboxUnavailable(
            "secure remote-only networking is unavailable with the Linux sandbox"
        )
    command = [
        executable,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    for path in _existing(["/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt"]):
        command.extend(["--ro-bind", str(path), str(path)])
    for path in _existing([
        "/etc/ssl",
        "/etc/hosts",
        "/etc/resolv.conf",
        "/etc/nsswitch.conf",
        "/etc/passwd",
        "/etc/group",
    ]):
        command.extend(["--ro-bind", str(path), str(path)])
    command.extend([
        "--bind",
        str(workspace),
        str(workspace),
        "--bind",
        str(sandbox_home),
        str(sandbox_home),
        "--chdir",
        str(workspace),
        "--",
        *argv,
    ])
    return SandboxInvocation(argv=command, backend="linux-bubblewrap")


def _windows_invocation(
    workspace: Path,
    sandbox_home: Path,
    argv: list[str],
    *,
    allow_network: bool,
) -> SandboxInvocation:
    """Route untrusted code through the trusted Windows AppContainer helper."""

    network_mode = "internet-client" if allow_network else "none"
    memory_mb = _bounded_windows_setting("APPFORGE_WINDOWS_SANDBOX_MEMORY_MB", 4096, 256, 32768)
    max_processes = _bounded_windows_setting("APPFORGE_WINDOWS_SANDBOX_MAX_PROCESSES", 64, 1, 512)
    cpu_rate = _bounded_windows_setting("APPFORGE_WINDOWS_SANDBOX_CPU_RATE", 8000, 100, 10000)
    return SandboxInvocation(
        argv=[
            sys.executable,
            "-m",
            "appforge.tooling.windows_sandbox",
            "--workspace",
            str(workspace.resolve()),
            "--sandbox-home",
            str(sandbox_home.resolve()),
            f"--network={network_mode}",
            f"--memory-mb={memory_mb}",
            f"--max-processes={max_processes}",
            f"--cpu-rate={cpu_rate}",
            "--",
            *argv,
        ],
        backend="windows-appcontainer-job",
    )


def _bounded_windows_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ExecutionSandboxUnavailable(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ExecutionSandboxUnavailable(f"{name} must be between {minimum} and {maximum}")
    return value


def sandbox_invocation(
    workspace: Path,
    sandbox_home: Path,
    argv: list[str],
    *,
    allow_network: bool,
) -> SandboxInvocation:
    system = platform.system()
    if system == "Darwin":
        return _macos_invocation(
            workspace,
            sandbox_home,
            argv,
            allow_network=allow_network,
        )
    if system == "Linux":
        return _linux_invocation(
            workspace,
            sandbox_home,
            argv,
            allow_network=allow_network,
        )
    if system == "Windows":
        return _windows_invocation(
            workspace,
            sandbox_home,
            argv,
            allow_network=allow_network,
        )
    raise ExecutionSandboxUnavailable(f"no secure project execution sandbox for {system}")


_WINDOWS_TOOLCHAIN_EXECUTABLES = (
    "python.exe",
    "python3.exe",
    "py.exe",
    "node.exe",
    "npm.cmd",
    "npx.cmd",
    "bun.exe",
    "git.exe",
    "pnpm.cmd",
    "yarn.cmd",
    "cargo.exe",
    "go.exe",
    "gradle.bat",
    "mvn.cmd",
    "flutter.bat",
    "dart.exe",
    "dotnet.exe",
    "java.exe",
    "javac.exe",
)


def approved_windows_path_entries(
    workspace: Path,
    *,
    environ: dict[str, str] | None = None,
    finder=None,
) -> list[str]:
    """Build a minimal Windows PATH from explicit project and toolchain roots.

    The host PATH is used only as input to ``shutil.which`` for a fixed executable
    allow-list.  Arbitrary user PATH entries are never copied into the sandbox.
    """

    environment = os.environ if environ is None else environ
    find_executable = shutil.which if finder is None else finder
    host_path = environment.get("PATH", "")
    system_root = environment.get("SystemRoot") or environment.get("WINDIR") or r"C:\Windows"
    candidates = [
        str(workspace),
        str(workspace / ".venv" / "Scripts"),
        str(workspace / "node_modules" / ".bin"),
        ntpath.join(system_root, "System32"),
        system_root,
        ntpath.join(system_root, "System32", "WindowsPowerShell", "v1.0"),
    ]
    if os.name == "nt":
        candidates.extend([str(Path(sys.executable).parent), str(Path(sys.prefix) / "Scripts")])
    for executable in _WINDOWS_TOOLCHAIN_EXECUTABLES:
        resolved = find_executable(executable, path=host_path)
        if resolved:
            candidates.append(ntpath.dirname(resolved))

    entries: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        if not raw:
            continue
        value = ntpath.normpath(str(raw))
        key = ntpath.normcase(value)
        if key in seen:
            continue
        seen.add(key)
        entries.append(value)
    return entries


def sanitized_path(workspace: Path) -> str:
    """Drop user-home tool directories from PATH before running project code."""

    if platform.system() == "Windows":
        return ";".join(approved_windows_path_entries(workspace))

    allowed_roots = [
        workspace.resolve(),
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/opt/homebrew"),
        Path("/opt/local"),
        Path("/usr/local"),
    ]
    entries: list[str] = []
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        if not raw:
            continue
        path = Path(raw).resolve()
        if any(path == root or root in path.parents for root in allowed_roots):
            value = str(path)
            if value not in entries:
                entries.append(value)
    for default in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if default not in entries and Path(default).is_dir():
            entries.append(default)
    return os.pathsep.join(entries)
