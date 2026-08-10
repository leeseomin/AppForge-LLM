from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hosted_windows_ci_covers_native_runtime_and_all_subprojects() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows-ci.yml").read_text(encoding="utf-8")

    for contract in (
        "windows-2025",
        "actions/checkout@v7",
        "actions/setup-python@v7",
        "actions/setup-node@v7",
        "bun-version-file: .bun-version",
        ".\\build.ps1 --check",
        "python.exe -m pytest -q",
        "tests/test_windows_pipeline_e2e.py",
        "npm --prefix frontend run build",
        "bun run typecheck",
        "bun test",
        "build.bat --help",
        ".\\build.ps1 --smoke --no-open",
    ):
        assert contract in workflow


def test_real_windows_11_smoke_is_available_as_a_self_hosted_manual_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows11-release-smoke.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch" in workflow
    assert "runs-on: [self-hosted, Windows, X64, appforge-win11]" in workflow
    assert "DPAPI" in workflow
    assert "AppContainer" in workflow


def test_bun_runtime_is_pinned() -> None:
    version = (ROOT / ".bun-version").read_text(encoding="utf-8").strip()

    assert version
    assert version.count(".") == 2
