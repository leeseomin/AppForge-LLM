# Changelog

## 0.5.0 — 2026-06-27

- Added a repository-root `build.sh` source launcher that prepares dependencies with `uv sync` or a pip fallback, builds packaged web assets, opens the local web UI, supports `--no-open`, and provides a bounded `--smoke` verification mode.
- Added the v4 engineering spine: Specification → Workflow → Memory → Loop Engineering.
- Added a local `llm-bridge` provider path with web Provider Settings, FastAPI `/api/llm` proxy routes, a Bun bridge service, and an `APPFORGE_DRIVER=llm-bridge` driver.
- Updated all built-in pipeline manifests to version 1.1 with strengthened or inserted `workflow_design`, `memory_engineering`, and `loop_engineering` stages before implementation-oriented work.
- Strengthened `requirements_spec` and `workflow_spec` schemas with assumptions, risks, quality gates, state model, compensation, concurrency, timeout, and traceability requirements.
- Added `memory_spec` and `loop_spec` artifact schemas plus dedicated stage skills.
- Added persistent runner memory in `.appforge/memory/stage-memory.jsonl` and injects a redacted summary into later stage packets.
- Added repeated-failure signature detection with `REPEATED_FAILURE_LOOP` to avoid unproductive retry loops.
- Updated web status copy, docs, tests, and packaged UI strings for v4.

## 0.3.0 — 2026-06-26

- Rebuilt the web frontend as a Vite + Vue single-page app under `frontend/`.
- Added Vue components for the composer, health banner, stage timeline, event feed, error panel, and toast notifications.
- Added SPA-friendly FastAPI static serving for Vite assets, manifest, favicon, and browser refresh fallback.
- Preserved the existing backend job manager, pipeline runner, pipeline manifests, gates, retries, and ZIP validation logic.
- Updated packaging to include recursive Vite build assets under `appforge/resources/web/`.

## 0.2.0 — 2026-06-26

- Added the minimal single-flow `appforge web` user experience.
- Added automatic driver readiness, pipeline routing, and one-active-job control.
- Added per-stage pending/running/validating/retrying/completed/failed status reporting.
- Added structured runner events and detailed failure payloads with redacted agent output and failed gates.
- Added persisted web-job state and refresh recovery.
- Added verified ZIP download activation only after full pipeline completion.
- Added responsive, accessible, dependency-free browser UI with no external assets.
- Added FastAPI integration and end-to-end web tests.

## 0.1.0 — 2026-06-25

- Initial OpenAppForge release.
- Twelve software production pipelines.
- Repository-native stage skills and assistant adapters.
- Codex, Claude Code, and generic command drivers.
- Schema-validated artifacts, deterministic gates, retries, approvals, and checkpoints.
- Safety-controlled tools for quality, security, compliance, and release packaging.
- One-command `appforge forge` workflow and manual agent-native loop.
