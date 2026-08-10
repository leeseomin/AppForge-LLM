from __future__ import annotations

from pathlib import Path

import pytest

from appforge.tooling.command import CommandPolicy, canonical_executable, validate_command


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"C:\\Windows\\System32\\cmd.exe", "cmd"),
        (r"C:\\Program Files\\PowerShell\\7\\pwsh.EXE", "pwsh"),
        (r"C:\\Program Files\\nodejs\\npm.cmd", "npm"),
        (r".\\gradlew.bat", "gradlew"),
        ("git.exe", "git"),
        ("tool.exe.cmd", "tool"),
        ("cmd.exe.", "cmd"),
    ],
)
def test_canonical_executable_strips_windows_launcher_suffixes(raw: str, expected: str) -> None:
    assert canonical_executable(raw) == expected


@pytest.mark.parametrize(
    "command",
    [
        ["cmd.exe", "/d", "/c", "del", "/q", "important.txt"],
        ["powershell.exe", "-NoProfile", "-Command", "Remove-Item important.txt"],
        ["pwsh.exe", "-NoProfile", "-Command", "Clear-Content important.txt"],
        ["python.exe", "-c", "print('inline')"],
        ["py.exe", "-3.11", "-c", "print('inline')"],
    ],
)
def test_windows_shell_and_inline_interpreter_aliases_require_destructive_opt_in(
    command: list[str],
    tmp_path: Path,
) -> None:
    with pytest.raises(PermissionError):
        validate_command(command, CommandPolicy(), workspace=tmp_path)


@pytest.mark.parametrize(
    "command",
    [
        ["del.exe", "important.txt"],
        ["erase.com", "important.txt"],
        ["rd.exe", "/s", "/q", "output"],
        ["rmdir.exe", "/s", "/q", "output"],
        ["diskpart.exe", "/s", "commands.txt"],
        ["format.com", "D:"],
    ],
)
def test_direct_windows_destructive_commands_are_blocked(command: list[str], tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        validate_command(command, CommandPolicy(), workspace=tmp_path)


def test_windows_package_manager_launcher_obeys_dependency_install_policy(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        validate_command(["npm.cmd", "install"], CommandPolicy(), workspace=tmp_path)

    with pytest.raises(PermissionError):
        validate_command(
            ["npm.cmd", "install"],
            CommandPolicy(allow_network=True),
            workspace=tmp_path,
        )

    validate_command(
        ["npm.cmd", "install"],
        CommandPolicy(allow_dependency_install=True),
        workspace=tmp_path,
    )


@pytest.mark.parametrize(
    "command",
    [
        ["gradlew.bat", "dependencies"],
        ["mvnw.cmd", "dependency:go-offline"],
        ["dotnet.exe", "restore"],
    ],
)
def test_other_windows_dependency_resolvers_require_dedicated_opt_in(
    tmp_path: Path,
    command: list[str],
) -> None:
    with pytest.raises(PermissionError):
        validate_command(command, CommandPolicy(allow_network=True), workspace=tmp_path)

    validate_command(
        command,
        CommandPolicy(allow_dependency_install=True),
        workspace=tmp_path,
    )


def test_workspace_python_pip_install_requires_the_dependency_capability(tmp_path: Path) -> None:
    interpreter = tmp_path / ".venv" / "Scripts" / "python.exe"

    with pytest.raises(PermissionError):
        validate_command(
            [str(interpreter), "-I", "-m", "pip", "install", "-e", "."],
            CommandPolicy(allow_network=True),
            workspace=tmp_path,
        )

    validate_command(
        [
            str(interpreter),
            "-I",
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "-e",
            ".",
        ],
        CommandPolicy(allow_dependency_install=True),
        workspace=tmp_path,
    )


def test_python_uppercase_environment_flag_is_not_mistaken_for_inline_eval(
    tmp_path: Path,
) -> None:
    validate_command(["python.exe", "-E", "--version"], CommandPolicy(), workspace=tmp_path)


def test_host_python_and_direct_pip_mutation_stay_behind_destructive_opt_in(
    tmp_path: Path,
) -> None:
    with pytest.raises(PermissionError):
        validate_command(
            ["python.exe", "-I", "-m", "pip", "install", "package"],
            CommandPolicy(allow_dependency_install=True, allow_network=True),
            workspace=tmp_path,
        )

    with pytest.raises(PermissionError):
        validate_command(
            ["pip.exe", "install", "package"],
            CommandPolicy(allow_network=True),
            workspace=tmp_path,
        )


def test_workspace_python_cannot_redirect_pip_to_a_host_interpreter(tmp_path: Path) -> None:
    interpreter = tmp_path / ".venv" / "Scripts" / "python.exe"

    with pytest.raises(PermissionError):
        validate_command(
            [
                str(interpreter),
                "-m",
                "pip",
                "--python",
                r"C:\\Users\\owner\\host-python.exe",
                "install",
                "package",
            ],
            CommandPolicy(allow_dependency_install=True, allow_network=True),
            workspace=tmp_path,
        )


def test_npx_requires_network_unless_it_is_explicitly_local_only(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        validate_command(["npx.cmd", "tsc", "--noEmit"], CommandPolicy(), workspace=tmp_path)

    validate_command(
        ["npx.cmd", "--no-install", "tsc", "--noEmit"],
        CommandPolicy(),
        workspace=tmp_path,
    )


def test_docker_registry_operations_obey_network_policy(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        validate_command(["docker.exe", "pull", "example/image"], CommandPolicy(), workspace=tmp_path)

    validate_command(
        ["docker.exe", "pull", "example/image"],
        CommandPolicy(allow_network=True),
        workspace=tmp_path,
    )


def test_windows_git_launcher_obeys_network_policy(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        validate_command(
            ["git.exe", "clone", "https://example.test/repository.git"],
            CommandPolicy(),
            workspace=tmp_path,
        )

    validate_command(
        ["git.exe", "clone", "https://example.test/repository.git"],
        CommandPolicy(allow_network=True),
        workspace=tmp_path,
    )


@pytest.mark.parametrize(
    "command",
    [
        [r"C:\Program Files\Git\cmd\git.exe", "reset", "--hard"],
        [r"C:\Program Files\Git\cmd\git.exe", "clean", "-fd"],
        [r"C:\Program Files\Git\cmd\git.exe", "push", "--force-with-lease"],
        [r"C:\Program Files\Git\cmd\git.exe", "-c", "safe.directory=*", "reset", "--hard"],
        [r"C:\Program Files\Git\cmd\git.exe", "push", "--force-with-lease=main"],
        [r"C:\Windows\System32\shutdown.exe", "/s"],
        [r"C:\Windows\System32\sudo.exe", "cmd.exe"],
    ],
)
def test_windows_suffixes_cannot_bypass_destructive_policy(
    tmp_path: Path,
    command: list[str],
) -> None:
    with pytest.raises(PermissionError):
        validate_command(command, CommandPolicy(), workspace=tmp_path)


def test_non_network_npm_script_remains_available_without_network(tmp_path: Path) -> None:
    validate_command(["npm.cmd", "test"], CommandPolicy(), workspace=tmp_path)


def test_typescript_fallback_uses_local_npx_without_registry_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from appforge.tooling import detection

    (tmp_path / "package.json").write_text(
        '{"devDependencies":{"typescript":"^5.0.0"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(detection, "command_exists", lambda _command: True)

    assert detection.quality_commands(tmp_path)["typecheck"] == [
        "npx",
        "--no-install",
        "tsc",
        "--noEmit",
    ]


def test_windows_gradle_wrapper_prefers_gradlew_bat(tmp_path: Path) -> None:
    from appforge.tooling.detection import gradle_wrapper

    (tmp_path / "gradlew.bat").write_text("@echo off\r\n", encoding="utf-8")

    assert gradle_wrapper(tmp_path, system_name="nt") == "gradlew.bat"


def test_posix_gradle_wrapper_prefers_executable_script(tmp_path: Path) -> None:
    from appforge.tooling.detection import gradle_wrapper

    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")

    assert gradle_wrapper(tmp_path, system_name="posix") == "./gradlew"


def test_protected_environment_names_are_case_insensitive(tmp_path: Path) -> None:
    from appforge.tooling.command import run_command

    with pytest.raises(PermissionError):
        run_command(
            tmp_path,
            ["python", "--version"],
            policy=CommandPolicy(),
            env={"Path": r"C:\\untrusted"},
        )
