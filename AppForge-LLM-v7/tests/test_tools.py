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
        {"command": [sys.executable, "--version"], "allow_destructive": True},
        project,
    )

    assert not result.success
    assert "allow_destructive" in str(result.error)


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
