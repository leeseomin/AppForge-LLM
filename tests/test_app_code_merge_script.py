from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _fixture_app(tmp_path: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    app_dir = tmp_path / "demo-app"
    app_dir.mkdir()
    for name in ("app-code-merge.py", "app-code-merge.sh", "app-code-merge.ps1"):
        shutil.copy(repo_root / name, app_dir / name)

    (app_dir / "src").mkdir()
    (app_dir / "src" / "main.ts").write_text(
        "export const answer = 42;\n",
        encoding="utf-8",
    )
    (app_dir / "app").mkdir()
    (app_dir / "app" / "server.py").write_text(
        "def handler():\n    return 'ok'\n",
        encoding="utf-8",
    )
    (app_dir / "package.json").write_text('{"scripts":{"dev":"vite"}}\n', encoding="utf-8")
    (app_dir / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    (app_dir / "Cargo.lock").write_text('version = 3\n', encoding="utf-8")
    (app_dir / ".env").write_text("SECRET_TOKEN=do-not-merge\n", encoding="utf-8")
    (app_dir / "private.pem").write_text("PRIVATE_KEY=do-not-merge\n", encoding="utf-8")
    (app_dir / "node_modules").mkdir()
    (app_dir / "node_modules" / "ignored.js").write_text(
        "ignored dependency\n",
        encoding="utf-8",
    )
    (app_dir / "dist").mkdir()
    (app_dir / "dist" / "bundle.js").write_text("ignored build output\n", encoding="utf-8")
    return app_dir


def test_app_code_merge_combines_current_folder_source_cross_platform(tmp_path: Path) -> None:
    app_dir = _fixture_app(tmp_path)

    completed = subprocess.run(
        [sys.executable, "app-code-merge.py"],
        cwd=app_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    merged = app_dir / "demo-app-source-code.md"
    assert merged.exists()
    assert not (app_dir / "demo-app-source.zip").exists()

    content = merged.read_text(encoding="utf-8")
    assert "## File: src/main.ts" in content
    assert "export const answer = 42;" in content
    assert "## File: app/server.py" in content
    assert "def handler():" in content
    assert "## File: package.json" in content
    assert "## File: Cargo.toml" in content
    assert "## File: Cargo.lock" in content
    assert "node_modules/ignored.js" not in content
    assert "dist/bundle.js" not in content
    assert "SECRET_TOKEN" not in content
    assert "PRIVATE_KEY" not in content
    assert "app-code-merge.py" not in content.split("Files merged:", maxsplit=1)[-1]


def test_platform_wrappers_delegate_to_the_cross_platform_implementation() -> None:
    root = Path(__file__).resolve().parents[1]

    shell = (root / "app-code-merge.sh").read_text(encoding="utf-8")
    powershell = (root / "app-code-merge.ps1").read_text(encoding="utf-8")

    assert "app-code-merge.py" in shell
    assert "app-code-merge.py" in powershell
    assert ".venv\\Scripts\\python.exe" in powershell
    assert "sys.version_info >= (3, 11)" in powershell
    assert "exit $LASTEXITCODE" in powershell
