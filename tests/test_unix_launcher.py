from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "Unix launcher contract")
@unittest.skipUnless(shutil.which("bun"), "Bun is required by the managed bridge")
@unittest.skipUnless((ROOT / ".venv" / "bin" / "appforge").is_file(), "Project venv is required")
@unittest.skipUnless(
    (ROOT / "appforge" / "resources" / "web" / "index.html").is_file(),
    "Packaged frontend assets are required",
)
class UnixLauncherContractTests(unittest.TestCase):
    def _run_check(
        self,
        *,
        occupied_port: int,
        bridge_url: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "APPFORGE_LLM_BRIDGE_URL",
            "APPFORGE_SKIP_LLM_BRIDGE",
            "APPFORGE_START_LLM_BRIDGE",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "APPFORGE_DRIVER": "llm-bridge-agent",
                "APPFORGE_SKIP_INSTALL": "1",
                "APPFORGE_SKIP_FRONTEND_BUILD": "1",
                "APPFORGE_LLM_BRIDGE_PORT_FALLBACK_LIMIT": "3",
            }
        )
        if bridge_url is not None:
            environment["APPFORGE_LLM_BRIDGE_URL"] = bridge_url

        with tempfile.TemporaryDirectory() as temporary_directory:
            fake_lsof = Path(temporary_directory) / "lsof"
            fake_lsof.write_text(
                "#!/bin/sh\n"
                f'case "$*" in *"-iTCP:{occupied_port}"*) exit 0 ;; *) exit 1 ;; esac\n',
                encoding="utf-8",
            )
            fake_lsof.chmod(0o700)
            environment["PATH"] = f"{temporary_directory}{os.pathsep}{environment['PATH']}"

            return subprocess.run(
                ["bash", str(ROOT / "build.sh"), "--check"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

    def test_managed_default_bridge_uses_a_fallback_when_port_8788_is_busy(self) -> None:
        completed = self._run_check(occupied_port=8788)

        output = completed.stdout + completed.stderr
        self.assertEqual(0, completed.returncode, output)
        self.assertRegex(
            output,
            r"LLM bridge port 8788 is already in use; using (?:8789|8790|8791) instead\.",
        )

    def test_explicit_bridge_url_is_not_reassigned_when_its_port_is_busy(self) -> None:
        completed = self._run_check(
            occupied_port=9788,
            bridge_url="http://127.0.0.1:9788",
        )

        output = completed.stdout + completed.stderr
        self.assertEqual(0, completed.returncode, output)
        self.assertNotIn("LLM bridge port", output)


if __name__ == "__main__":
    unittest.main()
