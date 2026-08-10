# Verification report

Date: 2026-08-10
Version: 0.7.0 — Windows 11 support build

## Scope

This verification covers the Windows command-policy hardening, AppContainer/Job Object routing, Windows PATH and Gradle handling, DPAPI secret storage, Windows launchers and workflows, and the cross-platform source merge helper.

## Commands executed

```bash
python3 -m pytest -o addopts='' -q \
  tests/test_windows_command_policy.py \
  tests/test_windows_sandbox.py \
  tests/test_windows_ci.py \
  tests/test_windows_launcher.py \
  tests/test_windows_pipeline_e2e.py \
  tests/test_app_code_merge_script.py

python3 -m pytest -o addopts='' -q -k '<exclude the 13 Bubblewrap-dependent integration cases>'
python3 -m pytest -o addopts='' -q
python3 -m compileall -q appforge tests app-code-merge.py

# Parse every GitHub Actions workflow with PyYAML.
# Strict-check llm_bridge/src/config.ts with TypeScript 5.8.3 and temporary
# Node API declarations; no repository files or lockfiles are changed.
```

## Results

- Focused Windows/security/launcher suite: **56 passed, 7 skipped**. The skips are native-Windows checks that cannot run on this Linux host.
- All tests not requiring the unavailable Linux Bubblewrap executable: **202 passed, 9 skipped, 13 deselected**.
- Unfiltered Python suite: **202 passed, 9 skipped, 13 failed**. Every failure follows the existing fail-closed `EXECUTION_SANDBOX_UNAVAILABLE` path because Bubblewrap is not installed in this execution environment; no additional regression category appeared.
- Python bytecode compilation: **passed**.
- `windows-ci.yml` and `windows11-release-smoke.yml` YAML parsing and job-shape validation: **passed**.
- DPAPI configuration module strict TypeScript check: **passed**.
- Dependency versions and lockfiles: **unchanged**.

## Validation boundary

This environment is Linux, so it cannot execute the Win32 `CreateAppContainerProfile`, `STARTUPINFOEX`, DPAPI CurrentUser, or Job Object runtime paths directly. Those paths are guarded by Windows-only integration tests and are wired into:

- hosted `windows-2025` CI for every push and pull request;
- the manual self-hosted Windows 11 release smoke workflow.

Bun/bridge and frontend dependency installation could not be repeated locally because the configured package registry was unavailable. Their dependency and lock files were not modified; the Windows CI workflows install them from their pinned manifests and run the full bridge/frontend suites.
