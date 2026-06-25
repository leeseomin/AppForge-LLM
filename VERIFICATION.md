# Verification record

Date: 2026-06-25
Version: 0.1.0

## Automated checks

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=. python -m pytest -q` — **17 passed**.
- Full deterministic fixture run — **7/7 prototype stages completed**, including implementation, `npm test`, production build, checkpointing, handoff, and source archive creation.
- `python -m pip wheel . --no-deps --no-build-isolation -w dist` — wheel built successfully.
- Wheel installed into an isolated target directory — `appforge version` and Korean bugfix routing smoke checks passed.
- Framework self secret scan — no findings.
- Python bytecode compilation — passed.

## Shipped production surface

- Pipelines: 12
- Auto-discovered tools: 25
- Markdown skills: 56
- Artifact schemas: 22
- Built wheel: `openappforge-0.1.0-py3-none-any.whl`
- Wheel SHA-256: `ee95dbabcc81c2fda4262033466a15d9f62e7b5cc0c3c8fabf72f8d644dfcffb`

## Environment limitation

Codex CLI and Claude Code CLI were not installed in the build environment, so their live model executions were not performed. Their adapters are covered by construction/unit checks and use the current non-interactive command shapes documented by their vendors. The generic driver was exercised end to end.
