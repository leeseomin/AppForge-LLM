from __future__ import annotations

import sys
import zipfile

import pytest

from appforge.drivers import LLMBridgeAgentDriver
from appforge.models import ProjectLayout
from appforge.tooling.detection import quality_commands
from appforge.tooling.command import CommandPolicy, run_command
from appforge.tooling.registry import ToolRegistry
from appforge.util import safe_resolve


def test_registry_discovers_unique_tool_contracts() -> None:
    registry = ToolRegistry()
    names = registry.names()
    assert len(names) >= 20
    assert len(names) == len(set(names))
    assert {"run_tests", "secret_scan", "archive_workspace", "release_readiness"}.issubset(names)


def test_python_quality_commands_use_current_interpreter(tmp_path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires = []\nbuild-backend = 'setuptools.build_meta'\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    monkeypatch.setenv("PATH", "")

    commands = quality_commands(tmp_path)

    assert commands["tests"] is not None
    assert commands["tests"][0] == sys.executable
    assert commands["build"] is not None
    assert commands["build"][0] == sys.executable


def test_workspace_escape_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError):
        safe_resolve(tmp_path, "../outside.txt")


def test_destructive_tools_require_explicit_opt_in(tmp_path) -> None:
    tool = ToolRegistry().get("write_text")
    denied = tool.run(tmp_path, {"path": "hello.txt", "content": "hello"})
    assert not denied.success
    allowed = tool.run(
        tmp_path,
        {"path": "hello.txt", "content": "hello", "allow_destructive": True},
    )
    assert allowed.success
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello"


def test_secret_scan_redacts_preview(tmp_path) -> None:
    secret = "ghp_" + ("a" * 36)
    key_name = "to" + "ken"
    (tmp_path / "config.txt").write_text(f'{key_name} = "{secret}"\n', encoding="utf-8")
    result = ToolRegistry().get("secret_scan").run(tmp_path, {})
    assert not result.success
    rendered = str(result.data)
    assert secret not in rendered
    assert "redacted" in rendered


def test_archive_excludes_secret_material_and_internal_state(tmp_path) -> None:
    (tmp_path / ".appforge" / "reports").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src" / ".DS_Store").write_text("mac metadata\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("TOKEN=secret\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("TOKEN=your-api-key\n", encoding="utf-8")
    (tmp_path / "server.pem").write_text("private\n", encoding="utf-8")
    (tmp_path / ".appforge" / "project.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".appforge-web" / "jobs").mkdir(parents=True)
    (tmp_path / ".appforge-web" / "jobs" / "job.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "projects" / "generated").mkdir(parents=True)
    (tmp_path / "projects" / "generated" / "app.py").write_text("print('generated')\n", encoding="utf-8")
    (tmp_path / "package.egg-info").mkdir()
    (tmp_path / "package.egg-info" / "PKG-INFO").write_text("metadata\n", encoding="utf-8")

    result = ToolRegistry().get("archive_workspace").run(tmp_path, {})
    assert result.success
    archive_path = tmp_path / result.data["archive"]
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "src/app.py" in names
    assert "src/.DS_Store" not in names
    assert ".env.example" in names
    assert ".env" not in names
    assert ".env.local" not in names
    assert "server.pem" not in names
    assert not any(name.startswith(".appforge/") for name in names)
    assert not any(name.startswith(".appforge-web/") for name in names)
    assert not any(name.startswith("projects/") for name in names)
    assert not any(name.startswith("package.egg-info/") for name in names)


def test_command_policy_blocks_network_and_destructive_patterns(tmp_path) -> None:
    with pytest.raises(PermissionError):
        run_command(tmp_path, ["curl", "https://example.com"], policy=CommandPolicy())
    with pytest.raises(PermissionError):
        run_command(tmp_path, ["git", "reset", "--hard"], policy=CommandPolicy())


def test_command_policy_blocks_shell_escape_when_destructive_disabled(tmp_path) -> None:
    with pytest.raises(PermissionError):
        run_command(tmp_path, ["bash", "-lc", "echo ok"], policy=CommandPolicy())


def test_command_policy_blocks_inline_python_when_destructive_disabled(tmp_path) -> None:
    with pytest.raises(PermissionError):
        run_command(tmp_path, [sys.executable, "-c", "print('ok')"], policy=CommandPolicy())


def test_run_command_llm_schema_hides_policy_flags() -> None:
    params = ToolRegistry().get("run_command").info()["llm_parameters"]

    assert "allow_network" not in params["properties"]
    assert "allow_destructive" not in params["properties"]


def test_llm_tool_arguments_cannot_escalate_safety_flags(tmp_path) -> None:
    layout = ProjectLayout.from_root(tmp_path)
    project = {"safety": {"allow_destructive": False, "allow_network": False}}

    result = LLMBridgeAgentDriver(bridge_url="http://bridge.test")._execute_registered_tool(
        layout,
        "run_command",
        {"command": ["bash", "-lc", "echo escalated"], "allow_destructive": True},
        project,
    )

    assert not result.success
    assert "allow_destructive" in str(result.error)


def test_run_command_allows_non_destructive_diagnostics_without_opt_in(tmp_path) -> None:
    """The pipeline cannot run tests or builds if every command needs allow_destructive."""
    layout = ProjectLayout.from_root(tmp_path)
    project = {"safety": {"allow_destructive": False, "allow_network": False}}

    result = LLMBridgeAgentDriver(bridge_url="http://bridge.test")._execute_registered_tool(
        layout,
        "run_command",
        {"command": [sys.executable, "--version"]},
        project,
    )

    assert result.success, result.error


def test_run_command_still_blocks_data_destroying_executables(tmp_path) -> None:
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(PermissionError):
        run_command(tmp_path, ["rm", "-r", "keep.txt"], policy=CommandPolicy())

    assert (tmp_path / "keep.txt").exists()


def test_dependency_install_is_allowed_without_general_network(tmp_path) -> None:
    from appforge.tooling.command import validate_command

    policy = CommandPolicy(allow_dependency_install=True)
    validate_command(["npm", "install"], policy, workspace=tmp_path)

    with pytest.raises(PermissionError):
        validate_command(["git", "clone", "https://example.test/repo.git"], policy, workspace=tmp_path)


def test_pip_install_outside_workspace_still_requires_destructive(tmp_path) -> None:
    from appforge.tooling.command import validate_command

    policy = CommandPolicy(allow_dependency_install=True)
    with pytest.raises(PermissionError):
        validate_command([sys.executable, "-m", "pip", "install", "requests"], policy, workspace=tmp_path)

    workspace_python = tmp_path / ".venv" / "bin" / "python"
    workspace_python.parent.mkdir(parents=True)
    workspace_python.write_text("", encoding="utf-8")
    validate_command(
        [str(workspace_python), "-m", "pip", "install", "-r", "requirements.txt"],
        policy,
        workspace=tmp_path,
    )


def test_commands_run_non_interactively(tmp_path) -> None:
    """Watch-mode test runners never exit; CI=true is what makes them run once."""
    script = tmp_path / "show_env.py"
    script.write_text(
        "import os\nprint(os.environ.get('CI', 'missing'))\n",
        encoding="utf-8",
    )

    result = run_command(tmp_path, [sys.executable, "show_env.py"], policy=CommandPolicy())

    assert result.success, result.error
    assert result.data["stdout"].strip() == "true"


def test_explicit_env_overrides_non_interactive_defaults(tmp_path) -> None:
    script = tmp_path / "show_env.py"
    script.write_text(
        "import os\nprint(os.environ.get('CI', 'missing'))\n",
        encoding="utf-8",
    )

    result = run_command(
        tmp_path,
        [sys.executable, "show_env.py"],
        policy=CommandPolicy(),
        env={"CI": "false"},
    )

    assert result.data["stdout"].strip() == "false"


def test_quality_tool_reports_missing_toolchain_as_skipped(tmp_path) -> None:
    """Exit 127 means the check never ran; reporting it as a failure loops the agent."""
    (tmp_path / "package.json").write_text(
        '{"name": "app", "scripts": {"test": "vitest run"}}', encoding="utf-8"
    )

    result = ToolRegistry().get("run_tests").run(tmp_path, {})

    assert result.success
    assert result.data["skipped"] is True
    assert result.data["code"] == "TOOLCHAIN_UNAVAILABLE"
    assert "install_dependencies" in result.data["reason"]


def test_run_command_does_not_inherit_sensitive_host_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPFORGE_TEST_SECRET", "should-not-leak")
    script = tmp_path / "print_env.py"
    script.write_text(
        (
            "import os\n"
            "print(os.environ.get('APPFORGE_TEST_SECRET', 'missing'))\n"
            "print(os.environ.get('APPFORGE_EXPLICIT_ENV', 'missing'))\n"
        ),
        encoding="utf-8",
    )

    result = run_command(
        tmp_path,
        [sys.executable, str(script)],
        env={"APPFORGE_EXPLICIT_ENV": "allowed"},
    )

    assert result.success
    assert result.data["stdout"].splitlines() == ["missing", "allowed"]
