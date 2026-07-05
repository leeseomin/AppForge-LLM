# AppForge-LLM v6 Release Notes

This v6 build applies the previously missing parts of `AppForge-v5-개선가이드.md` on top of the v5 codebase.

## Implemented in v6

### Agent loop hardening

- Default `auto` driver now resolves to `llm-bridge-agent`.
- Bridge agent sessions preserve `responseFormat` and propagate `finish_reason` / `usage` in `done` events.
- Python LLM bridge client accepts `response_format` for `/generate`, `/stream`, and `/agent/start`.
- Agent driver adds structured-output hints, repeated identical tool-call loop guards, token budget checks, and per-tool timeout injection.

### Quality and repair

- Added `appforge/llm_review.py` for an optional independent low-cost LLM review pass on implementation, verification, fix, and regression stages.
- Runner now folds `block` review verdicts into the stage review result so they can trigger repair.
- Failure packets include stdout/stderr/error evidence tails and implicated file extraction.
- `questionary` is now gracefully optional in minimal test environments.

### Web UX loop

- Added job queue support with one running job and configurable queued jobs.
- Added `/api/jobs/{id}/revise` for feature/bugfix revision jobs based on an existing workspace.
- Added `/api/jobs/{id}/approve` to resume checkpoint-mode jobs after human approval.
- Added `/api/jobs/{id}/retry` to retry a failed stage from the web UI.
- Added artifact listing API and frontend artifact browser.
- Added workspace browser/code viewer frontend connection.
- Added autonomous/checkpoint mode selector in the composer.
- Error panel now exposes retry and automatic repair actions.

### Routing and lightweight track

- Added an explainable `select_pipeline()` router that attempts a structured LLM classifier when enabled and falls back to keyword/complexity routing.
- Router metadata is surfaced in job payloads.
- Existing `web-app-lite` support is connected to the v6 routing path.

### Frontend and packaging

- Frontend source updated for v6 APIs and rebuilt into `appforge/resources/web`.
- Project/package versions bumped to `0.6.0` and UI title updated to AppForge-LLM v6.

## Verification performed

```bash
PYTHONPATH=. pytest -q
# 81 passed

cd frontend && npm run build
# vue-tsc --noEmit && vite build passed
```

Bun-based `llm_bridge` tests were not executed in this container because Bun is not installed here. The TypeScript bridge source was updated for `responseFormat` and usage propagation, and Python-side bridge contract tests passed.
