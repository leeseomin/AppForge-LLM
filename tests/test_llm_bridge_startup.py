from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appforge import llm_bridge
from appforge.llm_bridge_process import LLMBridgeProcessManager, _bridge_environment


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_URL = "http://127.0.0.1:8788"


class LLMBridgeStartupTests(unittest.TestCase):
    def test_managed_bridge_keeps_required_windows_profile_environment(self) -> None:
        windows_environment = {
            "PATH": r"C:\Tools",
            "SYSTEMROOT": r"C:\Windows",
            "USERPROFILE": r"C:\Users\tester",
            "APPDATA": r"C:\Users\tester\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\tester\AppData\Local",
            "HOMEDRIVE": "C:",
            "HOMEPATH": r"\Users\tester",
            "USERNAME": "tester",
            "UNRELATED_DATABASE_PASSWORD": "must-not-cross-boundary",
        }

        with patch.dict(os.environ, windows_environment, clear=True):
            environment = _bridge_environment("127.0.0.1", "8788", "bridge-capability")

        for name in (
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "HOMEDRIVE",
            "HOMEPATH",
            "USERNAME",
        ):
            self.assertEqual(windows_environment[name], environment[name])
        self.assertNotIn("UNRELATED_DATABASE_PASSWORD", environment)

    def test_existing_bridge_is_ready_only_after_config_store_warmup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = LLMBridgeProcessManager(
                runtime_dir=Path(temporary),
                timeout=45.0,
            )
            try:
                with (
                    patch.object(manager, "_is_healthy", return_value=True),
                    patch.object(
                        llm_bridge,
                        "get_active",
                        return_value={"provider": None, "model": None},
                    ) as get_active,
                    patch.object(
                        llm_bridge,
                        "list_providers",
                        return_value={"providers": []},
                    ) as list_providers,
                ):
                    manager.ensure_running(BRIDGE_URL)

                get_active.assert_called_once()
                self.assertEqual(BRIDGE_URL, get_active.call_args.args[0])
                self.assertGreater(get_active.call_args.kwargs["timeout"], 0)
                self.assertLessEqual(get_active.call_args.kwargs["timeout"], 45.0)
                list_providers.assert_called_once()
                self.assertEqual(BRIDGE_URL, list_providers.call_args.args[0])
                self.assertGreater(list_providers.call_args.kwargs["timeout"], 0)
                self.assertLessEqual(list_providers.call_args.kwargs["timeout"], 45.0)
            finally:
                manager.shutdown()

    def test_config_warmup_failure_reports_managed_log_instead_of_manual_bun_steps(self) -> None:
        timeout_error = llm_bridge.BridgeError(
            "LLM 브릿지 요청이 시간 초과되었습니다: timed out",
            status_code=504,
            payload={"error": {"code": "BRIDGE_REQUEST_TIMEOUT"}},
        )
        with tempfile.TemporaryDirectory() as temporary:
            manager = LLMBridgeProcessManager(
                runtime_dir=Path(temporary),
                timeout=45.0,
            )
            try:
                with (
                    patch.object(manager, "_is_healthy", return_value=True),
                    patch.object(
                        llm_bridge,
                        "get_active",
                        return_value={"provider": None, "model": None},
                    ),
                    patch.object(llm_bridge, "list_providers", side_effect=timeout_error),
                ):
                    with self.assertRaises(llm_bridge.BridgeError) as caught:
                        manager.ensure_running(BRIDGE_URL)

                self.assertEqual("bridge_config_unavailable", caught.exception.payload["reason"])
                self.assertTrue(str(caught.exception.payload["log_path"]).endswith("llm-bridge.log"))
                self.assertNotIn("bun install", caught.exception.payload["action"])
            finally:
                manager.shutdown()

    def test_missing_bun_guidance_points_back_to_the_one_click_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root_dir = Path(temporary)
            bridge_source = root_dir / "llm_bridge" / "src" / "index.ts"
            bridge_source.parent.mkdir(parents=True)
            bridge_source.write_text("", encoding="utf-8")
            manager = LLMBridgeProcessManager(root_dir=root_dir)

            with (
                patch("appforge.llm_bridge_process.os.name", "nt"),
                patch("appforge.llm_bridge_process.command_exists", return_value=False),
            ):
                with self.assertRaises(llm_bridge.BridgeError) as caught:
                    manager._start_process(BRIDGE_URL)

            self.assertEqual("bun_missing", caught.exception.payload["reason"])
            self.assertIn("build.bat", caught.exception.payload["action"])
            self.assertNotIn("Bun을 설치", caught.exception.payload["action"])

    def test_missing_bridge_dependencies_guidance_points_back_to_the_one_click_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root_dir = Path(temporary)
            bridge_source = root_dir / "llm_bridge" / "src" / "index.ts"
            bridge_source.parent.mkdir(parents=True)
            bridge_source.write_text("", encoding="utf-8")
            manager = LLMBridgeProcessManager(root_dir=root_dir)

            with (
                patch("appforge.llm_bridge_process.os.name", "nt"),
                patch("appforge.llm_bridge_process.command_exists", return_value=True),
            ):
                with self.assertRaises(llm_bridge.BridgeError) as caught:
                    manager._start_process(BRIDGE_URL)

            self.assertEqual("node_modules_missing", caught.exception.payload["reason"])
            self.assertIn("build.bat", caught.exception.payload["action"])
            self.assertNotIn("bun install", caught.exception.payload["action"])

    def test_windows_launcher_allows_cold_bridge_startup_and_avoids_manual_guidance(self) -> None:
        launcher = (ROOT / "build.ps1").read_text(encoding="utf-8")
        readiness_source = (ROOT / "appforge" / "web_jobs.py").read_text(encoding="utf-8")
        catalog_source = (ROOT / "llm_bridge" / "src" / "catalog.ts").read_text(
            encoding="utf-8"
        )
        windows_guide = (ROOT / "docs" / "WINDOWS_11.md").read_text(encoding="utf-8")

        self.assertIn(
            'Get-EnvironmentValue "APPFORGE_BRIDGE_TIMEOUT" "45"',
            launcher,
        )
        self.assertIn(
            'Get-EnvironmentValue "APPFORGE_SMOKE_TIMEOUT" "60"',
            launcher,
        )
        self.assertIn("$env:APPFORGE_BRIDGE_TIMEOUT", launcher)
        self.assertNotIn("bun install` 후 `bun run dev", readiness_source)
        self.assertIn("브릿지는 자동으로 시작됩니다", readiness_source)
        self.assertIn("AbortSignal.timeout", catalog_source)
        self.assertIn(
            "Users do not need to run `bun install` or `bun run dev` manually",
            windows_guide,
        )


if __name__ == "__main__":
    unittest.main()
