# Verification report

Date: 2026-08-10  
Version: 0.7.0 — Windows 11 v2 remediation

## Scope

This verification covers the three reported Windows 11 gaps:

1. the missing hosted Windows CI and real-Windows-11 release-smoke workflows;
2. conversion of `build.ps1 --check` from a dependency/sandbox preflight into a complete Python + frontend + Bun verification gate;
3. fail-closed Windows DACL creation and validation for the LLM provider configuration and DPAPI ciphertext store.

It also covers the Windows test-path changes needed because a hosted runner may place `TEMP` outside the signed-in user's profile.

## Security invariant

Before AppForge trusts `providers.json` or decrypts a referenced DPAPI secret:

- the configuration directory must be a regular, non-reparse directory below the current Windows profile;
- every parent through the profile root must have a privileged owner and no effective write grant for another unprivileged identity;
- the configuration directory and existing configuration files must have a protected DACL owned by the current user;
- an existing object that is currently writable by another identity is rejected, not silently repaired and trusted;
- atomic temporary files are protected before rename, and the final file is verified after rename.

This is the integrity boundary implemented to close the reported path in which another account could alter an `openai-compatible` base URL in `providers.json` before the bridge hydrated the stored API key.

## Commands executed

```bash
python3 -m pytest -q --junitxml=/tmp/appforge-windows-focused-final.xml \
  tests/test_windows_command_policy.py \
  tests/test_windows_sandbox.py \
  tests/test_windows_ci.py \
  tests/test_windows_launcher.py \
  tests/test_windows_pipeline_e2e.py \
  tests/test_app_code_merge_script.py

python3 -m pytest -q --junitxml=/tmp/appforge-pytest-nobwrap-final.xml \
  <all tests, with the 13 Bubblewrap-dependent Linux integration cases deselected>

python3 -m pytest -q --tb=no --junitxml=/tmp/appforge-pytest-final.xml
python3 -m compileall -q appforge tests app-code-merge.py

# Parse both GitHub Actions workflows with PyYAML.

# Strict-check llm_bridge/src/config.ts and its focused ACL tests with
# TypeScript 5.8.3 and local Node declarations.

# Transpile the ACL module to an isolated temporary CommonJS directory and
# run a mocked end-to-end provider write through the real config API.

npm --prefix frontend run test:i18n
npm --prefix frontend ci --offline
npm --prefix frontend ci
```

## Results

- Focused Windows/security/launcher suite: **60 passed, 7 skipped, 0 failed** (`67` collected). The skips are native-Windows checks on this Linux host.
- All Python tests not requiring the unavailable Linux Bubblewrap executable: **206 passed, 9 skipped, 13 deselected, 0 failed**.
- Unfiltered Python suite: **206 passed, 9 skipped, 13 failed** (`228` collected). Every failure is an existing fail-closed `EXECUTION_SANDBOX_UNAVAILABLE` path caused by Bubblewrap not being installed; no new failure category appeared.
- Python bytecode compilation: **passed**.
- `.github/workflows/windows-ci.yml` and `.github/workflows/windows11-release-smoke.yml` YAML parsing/job-shape validation: **passed**.
- ACL/config source strict TypeScript check: **passed**.
- Focused ACL test TypeScript check: **passed**.
- ACL runtime mock through `setProvider`: **passed**, with one parent check, one protected-directory check, one atomic-temporary-file protection, and one final-file verification.
- Frontend Node 22 TypeScript localization tests: **3 passed, 0 failed**.
- Frontend dependency installation: **not available on this host**. Offline installation reported `ENOTCACHED`, and the configured internal registry returned `404` for `zwitch-2.0.4.tgz`. No lockfile was changed.
- Bun typecheck/tests: **not executed locally** because Bun is not installed in this Linux environment. The source and focused test files passed TypeScript validation, and both Windows workflows install the `.bun-version` runtime and execute the complete Bun suite.

## Windows-native validation boundary

This host cannot execute Windows PowerShell ACL mutation, DPAPI `CurrentUser`, AppContainer profile creation, Win32 `STARTUPINFOEX`, or Job Object runtime paths. The repository now contains executable native regressions for:

- a real DPAPI encrypt/decrypt round trip;
- protected owner/DACL assertions for the config directory, `providers.json`, and `secrets.dpapi.json`;
- non-ASCII Windows profile/configuration paths;
- rejection of a real parent DACL granting `Everyone: Modify`;
- rejection of a real provider file granting `Everyone: Modify` before secret hydration;
- AppContainer filesystem/loopback isolation and Job Object descendant cleanup.

Those checks are configured to run in the hosted `windows-2025` workflow. The manual `appforge-win11` self-hosted workflow is configured to additionally confirm that the runner is Windows 11 build 22000 or newer and re-run the real DPAPI/DACL and AppContainer tests before the web smoke check.
