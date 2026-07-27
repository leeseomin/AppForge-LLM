from __future__ import annotations

from typer.testing import CliRunner

from appforge.cli import app


runner = CliRunner()


def test_help_and_routing_commands() -> None:
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "forge" in help_result.stdout

    route_result = runner.invoke(app, ["route", "모바일 앱을 만들어라"])
    assert route_result.exit_code == 0
    assert "mobile-app" in route_result.stdout


def test_new_and_status_commands(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "new",
            "Build a small web app",
            "--name",
            "cli-smoke",
            "--pipeline",
            "web-app",
            "--projects-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    project = tmp_path / "cli-smoke"
    status = runner.invoke(app, ["status", str(project)])
    assert status.exit_code == 0
    assert "intake" in status.stdout
    assert "Next stage" in status.stdout
