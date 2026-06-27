from __future__ import annotations

from typer.testing import CliRunner

from appforge.cli import app

runner = CliRunner()


def test_auth_login_invokes_flow(monkeypatch):
    monkeypatch.setattr("appforge.llm_auth.cmd_login", lambda *a, **k: 0)
    result = runner.invoke(app, ["auth", "login", "--provider", "deepseek"])
    assert result.exit_code == 0


def test_auth_list_command(monkeypatch):
    monkeypatch.setattr("appforge.llm_auth.cmd_list", lambda *a, **k: 0)
    result = runner.invoke(app, ["auth", "list"])
    assert result.exit_code == 0


def test_auth_logout_command(monkeypatch):
    monkeypatch.setattr("appforge.llm_auth.cmd_logout", lambda *a, **k: 0)
    result = runner.invoke(app, ["auth", "logout", "deepseek"])
    assert result.exit_code == 0


def test_auth_use_command(monkeypatch):
    monkeypatch.setattr("appforge.llm_auth.cmd_use", lambda *a, **k: 0)
    result = runner.invoke(app, ["auth", "use", "deepseek", "--model", "deepseek-v4-pro"])
    assert result.exit_code == 0


def test_models_command(monkeypatch):
    monkeypatch.setattr("appforge.llm_auth.cmd_models", lambda *a, **k: 0)
    result = runner.invoke(app, ["models", "deepseek"])
    assert result.exit_code == 0


def test_models_refresh_flag(monkeypatch):
    monkeypatch.setattr("appforge.llm_auth.cmd_models", lambda *a, **k: 0)
    result = runner.invoke(app, ["models", "--refresh"])
    assert result.exit_code == 0


def test_auth_help_lists_subcommands():
    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "logout" in result.stdout
    assert "use" in result.stdout
