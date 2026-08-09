from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsLauncherContractTests(unittest.TestCase):
    def test_batch_file_is_a_double_click_wrapper(self) -> None:
        launcher = (ROOT / "build.bat").read_text(encoding="utf-8")

        self.assertIn("pwsh.exe", launcher)
        self.assertIn("powershell.exe", launcher)
        self.assertIn("-ExecutionPolicy Bypass", launcher)
        self.assertIn('-File "%~dp0build.ps1" %*', launcher)
        self.assertIn("pause", launcher.lower())

    def test_powershell_launcher_matches_the_supported_build_modes(self) -> None:
        launcher = (ROOT / "build.ps1").read_text(encoding="utf-8")

        for option in ("--smoke", "--check", "--no-open", "--help"):
            self.assertIn(option, launcher)
        for contract in (
            ".venv\\Scripts\\appforge.exe",
            "APPFORGE_SKIP_INSTALL",
            "APPFORGE_SKIP_FRONTEND_BUILD",
            "llm_bridge\\node_modules",
            "--frozen-lockfile",
            "--no-open-browser",
            "/api/health",
            "Invoke-WebRequest",
        ):
            self.assertIn(contract, launcher)

    def test_readme_exposes_the_one_click_windows_entrypoint(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Windows 11", readme)
        self.assertIn("build.bat", readme)

    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell is not installed in this environment",
    )
    def test_powershell_launcher_parses_without_errors(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        launcher = str(ROOT / "build.ps1").replace("'", "''")
        parse_command = (
            "$tokens = $null; $errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{launcher}', "
            "[ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { $errors | Out-String | Write-Error; exit 1 }"
        )

        completed = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-Command", parse_command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows-only launcher integration check")
    def test_batch_wrapper_can_show_powershell_help(self) -> None:
        environment = os.environ.copy()
        environment["APPFORGE_NO_PAUSE"] = "1"

        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", str(ROOT / "build.bat"), "--help"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("Usage: .\\build.ps1", completed.stdout)


if __name__ == "__main__":
    unittest.main()
