from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hosted_windows_ci_invokes_the_full_gate_and_native_smoke_paths() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows-ci.yml").read_text(encoding="utf-8")

    for contract in (
        "windows-2025",
        "actions/checkout@v7",
        "actions/setup-python@v7",
        "actions/setup-node@v7",
        "oven-sh/setup-bun@v2",
        "bun-version-file: .bun-version",
        ".\\build.ps1 --check",
        ".\\.venv\\Scripts\\python.exe -m pytest -q",
        "npm --prefix frontend run build",
        "bun run typecheck",
        "bun test",
        ".\\.venv\\Scripts\\python.exe -m pytest -q tests/test_windows_pipeline_e2e.py",
        "build.bat --help",
        ".\\build.ps1 --smoke --no-open",
    ):
        assert contract in workflow

    assert "permissions:\n  contents: read" in workflow


def test_build_check_is_a_real_cross_project_test_gate() -> None:
    launcher = (ROOT / "build.ps1").read_text(encoding="utf-8")

    for contract in (
        "function Invoke-CheckSuite",
        '"-m", "compileall", "-q", "appforge", "tests", "app-code-merge.py"',
        '"-m", "pytest", "-q"',
        '"--prefix", "frontend", "run", "test:i18n"',
        '"--prefix", "frontend", "run", "typecheck"',
        '"--prefix", "frontend", "run", "build"',
        'Invoke-CheckedCommand $script:BunBin @("run", "typecheck")',
        'Invoke-CheckedCommand $script:BunBin @("test")',
        "Ignoring APPFORGE_SKIP_INSTALL because --check is a full test gate.",
        "Ignoring APPFORGE_SKIP_FRONTEND_BUILD because --check is a full test gate.",
        "Full Windows check passed: sandbox, Python, frontend, and llm_bridge gates are green.",
    ):
        assert contract in launcher

    main = launcher.split("function Invoke-Main", maxsplit=1)[1]
    assert main.index("Test-WindowsSandboxRuntime") < main.index("Invoke-CheckSuite")
    assert main.index("Invoke-CheckSuite") < main.index("Select-ManagedBridgePort")

    check_suite = launcher.split("function Invoke-CheckSuite", maxsplit=1)[1].split(
        "function Get-WebArguments", maxsplit=1
    )[0]
    assert check_suite.index('"-m", "pytest", "-q"') < check_suite.index(
        '"--prefix", "frontend", "run", "test:i18n"'
    )
    assert check_suite.index('"--prefix", "frontend", "run", "build"') < check_suite.index(
        'Invoke-CheckedCommand $script:BunBin @("run", "typecheck")'
    )


def test_real_windows_11_smoke_is_available_as_a_self_hosted_manual_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows11-release-smoke.yml").read_text(
        encoding="utf-8"
    )

    for contract in (
        "workflow_dispatch",
        "runs-on: [self-hosted, Windows, X64, appforge-win11]",
        "Windows 11",
        "build 22000",
        "DPAPI",
        "DACL",
        "AppContainer",
        ".\\build.ps1 --check",
        "bun test test/config.test.ts",
        "tests/test_windows_pipeline_e2e.py",
        ".\\build.ps1 --smoke --no-open",
    ):
        assert contract in workflow

    assert "permissions:\n  contents: read" in workflow


def test_windows_acl_security_regressions_are_part_of_the_bridge_suite() -> None:
    source = (ROOT / "llm_bridge" / "src" / "config.ts").read_text(encoding="utf-8")
    tests = (ROOT / "llm_bridge" / "test" / "config.test.ts").read_text(encoding="utf-8")

    for contract in (
        "WINDOWS_ACL_POWERSHELL_SCRIPT",
        "[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)",
        "AreAccessRulesProtected",
        "Test-RepairablePrivateAcl",
        "Windows ACL validation failed for ${label}",
        "APPFORGE_LLM_CONFIG must use a dedicated directory below the Windows user profile",
        "await assertWindowsAcl(temporary, \"file\", \"private\", true)",
        "await assertSafeConfigFile(path, false)",
    ):
        assert contract in source

    for contract in (
        "fails closed before writing into an unsafe parent",
        "refuses to trust a provider file that may have been tampered with",
        "real CurrentUser round trip with private DACLs",
        "supports non-ASCII user and config paths",
        "real parent DACL writable by Everyone",
        "real provider file writable by Everyone before secret hydration",
    ):
        assert contract in tests


def test_frontend_i18n_test_runs_typescript_on_supported_node_22_builds() -> None:
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["test:i18n"] == (
        "node --experimental-strip-types --test tests/i18n.test.ts"
    )


def test_all_bridge_suites_use_a_profile_local_temp_root_on_windows() -> None:
    helper = (ROOT / "llm_bridge" / "test" / "test-paths.ts").read_text(encoding="utf-8")
    assert 'process.platform === "win32"' in helper
    assert 'join(homedir(), ".appforge-llm-test-tmp")' in helper

    for name in ("catalog.test.ts", "config.test.ts", "registry.test.ts", "server.test.ts"):
        source = (ROOT / "llm_bridge" / "test" / name).read_text(encoding="utf-8")
        assert "makeBridgeTestDirectory" in source


def test_bun_runtime_is_pinned() -> None:
    version = (ROOT / ".bun-version").read_text(encoding="utf-8").strip()

    assert version
    assert version.count(".") == 2
