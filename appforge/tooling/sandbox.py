from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


class ExecutionSandboxUnavailable(RuntimeError):
    """Raised when untrusted project code cannot be confined safely."""


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
    raise ExecutionSandboxUnavailable(f"no secure project execution sandbox for {system}")


def sanitized_path(workspace: Path) -> str:
    """Drop user-home tool directories from PATH before running project code."""

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
