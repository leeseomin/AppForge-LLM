from __future__ import annotations

import os
import ntpath
import platform
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from appforge.tooling.command import CommandPolicy, run_command
from appforge.tooling.sandbox import (
    WINDOWS_SANDBOX_ERROR_PREFIX,
    approved_windows_path_entries,
    sandbox_invocation,
)
from appforge.tooling.windows_sandbox_native import _safe_external_path_entries


def test_windows_sandbox_invocation_routes_through_trusted_helper(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "sandbox-home"
    workspace.mkdir()
    home.mkdir()
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    invocation = sandbox_invocation(
        workspace,
        home,
        ["python.exe", "--version"],
        allow_network=False,
    )

    assert invocation.backend == "windows-appcontainer-job"
    assert invocation.argv[:3] == [sys.executable, "-m", "appforge.tooling.windows_sandbox"]
    assert "--workspace" in invocation.argv
    assert str(workspace.resolve()) in invocation.argv
    assert "--sandbox-home" in invocation.argv
    assert str(home.resolve()) in invocation.argv
    assert "--network=none" in invocation.argv
    assert invocation.argv[-3:] == ["--", "python.exe", "--version"]


def test_windows_sandbox_invocation_only_enables_internet_client_when_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    invocation = sandbox_invocation(
        tmp_path,
        tmp_path / "home",
        ["npm.cmd", "install"],
        allow_network=True,
    )

    assert "--network=internet-client" in invocation.argv


def test_windows_path_is_built_from_workspace_and_approved_toolchains(tmp_path: Path) -> None:
    workspace = Path(r"C:\work\generated-app")
    fake_environment = {
        "SystemRoot": r"C:\Windows",
        "ProgramFiles": r"C:\Program Files",
        "PATH": ";".join(
            [
                r"C:\Users\person\bin",
                r"C:\Program Files\nodejs",
                r"C:\Windows\System32",
            ]
        ),
    }
    resolved = {
        "python.exe": r"C:\Python311\python.exe",
        "node.exe": r"C:\Program Files\nodejs\node.exe",
        "npm.cmd": r"C:\Program Files\nodejs\npm.cmd",
        "git.exe": r"C:\Program Files\Git\cmd\git.exe",
    }

    def finder(name: str, *, path: str | None = None) -> str | None:
        del path
        return resolved.get(name)

    entries = approved_windows_path_entries(
        workspace,
        environ=fake_environment,
        finder=finder,
    )
    folded = {value.casefold() for value in entries}

    assert ntpath.join(str(workspace), ".venv", "Scripts").casefold() in folded
    assert ntpath.join(str(workspace), "node_modules", ".bin").casefold() in folded
    assert r"C:\Windows\System32".casefold() in folded
    assert r"C:\Python311".casefold() in folded
    assert r"C:\Program Files\nodejs".casefold() in folded
    assert r"C:\Program Files\Git\cmd".casefold() in folded
    assert r"C:\Users\person\bin".casefold() not in folded


def test_workspace_toolchain_symlink_cannot_expand_the_host_allow_list(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-toolchain"
    outside.mkdir()
    project_bin = workspace / "node_modules" / ".bin"
    project_bin.parent.mkdir()
    try:
        project_bin.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    assert _safe_external_path_entries(workspace, [str(project_bin)]) == []


def test_untrusted_stderr_cannot_spoof_a_windows_sandbox_setup_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        "appforge.tooling.command.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr=f"{WINDOWS_SANDBOX_ERROR_PREFIX}spoofed",
        ),
    )

    result = run_command(tmp_path, ["python.exe", "--version"], policy=CommandPolicy())

    assert result.error == "Command exited with 1"
    assert result.data.get("code") is None


def test_trusted_windows_helper_exit_maps_to_sandbox_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        "appforge.tooling.command.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=126,
            stdout="",
            stderr=f"{WINDOWS_SANDBOX_ERROR_PREFIX}AppContainer unavailable",
        ),
    )

    result = run_command(tmp_path, ["python.exe", "--version"], policy=CommandPolicy())

    assert result.data["code"] == "EXECUTION_SANDBOX_UNAVAILABLE"
    assert result.data["reason"] == "AppContainer unavailable"


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer integration check")
def test_windows_appcontainer_denies_sibling_file_and_allows_workspace_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "host-secret.txt"
    outside.write_text("must-not-be-readable", encoding="utf-8")
    script = workspace / "probe.py"
    script.write_text(
        "from pathlib import Path\n"
        f"outside = Path({str(outside)!r})\n"
        "try:\n"
        "    outside.read_text(encoding='utf-8')\n"
        "except OSError:\n"
        "    print('outside-denied')\n"
        "else:\n"
        "    print('outside-readable')\n"
        "Path('inside.txt').write_text('ok', encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = run_command(workspace, [sys.executable, "probe.py"], policy=CommandPolicy())

    assert result.success, result.data
    assert result.data["sandbox"] == "windows-appcontainer-job"
    assert result.data["stdout"].strip() == "outside-denied"
    assert (workspace / "inside.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer integration check")
def test_windows_appcontainer_blocks_host_loopback_without_network_capability(tmp_path: Path) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    script = tmp_path / "probe_loopback.py"
    script.write_text(
        "import socket\n"
        "sock = socket.socket()\n"
        f"print('blocked' if sock.connect_ex(('127.0.0.1', {port})) else 'reachable')\n",
        encoding="utf-8",
    )
    try:
        result = run_command(tmp_path, [sys.executable, script.name], policy=CommandPolicy())
    finally:
        listener.close()

    assert result.success, result.data
    assert result.data["stdout"].strip() == "blocked"


def test_native_windows_launcher_declares_appcontainer_network_and_job_boundaries() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "appforge"
        / "tooling"
        / "windows_sandbox_native.py"
    ).read_text(encoding="utf-8")

    for contract in (
        "CreateAppContainerProfile",
        "PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES",
        "WIN_CAPABILITY_INTERNET_CLIENT_SID",
        "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
        "JOB_OBJECT_LIMIT_ACTIVE_PROCESS",
        "JOB_OBJECT_LIMIT_JOB_MEMORY",
        "JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP",
        "AssignProcessToJobObject",
    ):
        assert contract in source


@pytest.mark.skipif(os.name != "nt", reason="Windows batch-launcher integration check")
def test_windows_appcontainer_executes_npm_cmd_without_exposing_cmd_shell(tmp_path: Path) -> None:
    import shutil

    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        pytest.skip("npm.cmd is unavailable")

    result = run_command(tmp_path, [npm, "--version"], policy=CommandPolicy())

    assert result.success, result.data
    assert result.data["sandbox"] == "windows-appcontainer-job"
    assert result.data["stdout"].strip()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration check")
def test_windows_timeout_closes_job_and_terminates_descendants(tmp_path: Path) -> None:
    import time

    sentinel = tmp_path / "child-survived.txt"
    child = tmp_path / "child.py"
    child.write_text(
        "import time\n"
        "from pathlib import Path\n"
        "time.sleep(3)\n"
        f"Path({str(sentinel)!r}).write_text('survived', encoding='utf-8')\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    result = run_command(
        tmp_path,
        [sys.executable, parent.name],
        policy=CommandPolicy(timeout_seconds=1),
    )
    time.sleep(4)

    assert not result.success
    assert result.data["timed_out"] is True
    assert not sentinel.exists()
