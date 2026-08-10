from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from appforge.tooling.registry import ToolRegistry


@pytest.mark.skipif(os.name != "nt", reason="native Windows AppContainer integration")
@pytest.mark.skipif(shutil.which("node") is None or shutil.which("npm") is None, reason="Node.js/npm required")
def test_windows_fixture_runs_tests_build_and_preview_artifact_without_host_file_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "fixture app 한글"
    workspace.mkdir()
    scripts = workspace / "scripts"
    scripts.mkdir()
    outside = tmp_path / "host-secret.txt"
    outside.write_text("must-not-be-readable", encoding="utf-8")
    monkeypatch.setenv("APPFORGE_WINDOWS_E2E_SECRET", "must-not-leak")

    (workspace / "package.json").write_text(
        json.dumps(
            {
                "name": "appforge-windows-fixture",
                "private": True,
                "scripts": {
                    "test": "node scripts/test.mjs",
                    "build": "node scripts/build.mjs",
                },
            }
        ),
        encoding="utf-8",
    )
    (scripts / "test.mjs").write_text(
        "import { readFileSync, writeFileSync } from 'node:fs';\n"
        f"const outside = {json.dumps(str(outside))};\n"
        "if (process.env.APPFORGE_WINDOWS_E2E_SECRET) process.exit(11);\n"
        "let denied = false;\n"
        "try { readFileSync(outside, 'utf8'); } catch { denied = true; }\n"
        "if (!denied) process.exit(12);\n"
        "writeFileSync('test-ok.txt', 'ok\\n', 'utf8');\n",
        encoding="utf-8",
    )
    (scripts / "build.mjs").write_text(
        "import { mkdirSync, writeFileSync } from 'node:fs';\n"
        "mkdirSync('dist', { recursive: true });\n"
        "writeFileSync('dist/index.html', '<!doctype html><title>Windows preview</title>', 'utf8');\n",
        encoding="utf-8",
    )

    registry = ToolRegistry()
    tests = registry.get("run_tests").run(workspace, {"timeout": 120})
    build = registry.get("run_build").run(workspace, {"timeout": 120})

    assert tests.success, tests.data
    assert tests.data["sandbox"] == "windows-appcontainer-job"
    assert (workspace / "test-ok.txt").read_text(encoding="utf-8") == "ok\n"
    assert build.success, build.data
    assert build.data["sandbox"] == "windows-appcontainer-job"
    assert "Windows preview" in (workspace / "dist" / "index.html").read_text(encoding="utf-8")
