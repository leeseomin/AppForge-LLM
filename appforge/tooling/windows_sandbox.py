"""Trusted Windows AppContainer launcher used by :mod:`appforge.tooling.sandbox`.

The native implementation is intentionally kept in this separate module so the
normal AppForge process never executes generated code directly on Windows.
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

from .sandbox import ExecutionSandboxUnavailable, WINDOWS_SANDBOX_ERROR_PREFIX


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--sandbox-home", required=True)
    parser.add_argument("--network", choices=("none", "internet-client"), default="none")
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument("--max-processes", type=int, default=64)
    parser.add_argument("--cpu-rate", type=int, default=8000)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(argv)
    if parsed.command[:1] == ["--"]:
        parsed.command = parsed.command[1:]
    if not parsed.command and not parsed.doctor:
        parser.error("a target command is required after --")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parsed = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if platform.system() != "Windows":
        raise ExecutionSandboxUnavailable("the AppContainer helper can run only on Windows")
    # Replaced below by the native ctypes implementation.  Keeping the entry point
    # explicit makes policy and packaging tests fail closed while that code loads.
    from .windows_sandbox_native import run_appcontainer_command

    command = [sys.executable, "--version"] if parsed.doctor else parsed.command
    return run_appcontainer_command(
        Path(parsed.workspace),
        Path(parsed.sandbox_home),
        command,
        allow_network=parsed.network == "internet-client",
        memory_mb=parsed.memory_mb,
        max_processes=parsed.max_processes,
        cpu_rate=parsed.cpu_rate,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExecutionSandboxUnavailable as exc:
        print(f"{WINDOWS_SANDBOX_ERROR_PREFIX}{exc}", file=sys.stderr)
        raise SystemExit(126)
