# AppForge-LLM v6 changelog

v6 closes the main gaps left after the v5 improvement guide implementation review. It keeps the v5 tool-use agent foundation and adds the missing product loop, routing, review, and safety wiring.

## Implemented

- Added queue-backed web jobs so new work can be accepted while one job is running.
- Added revision jobs through `POST /api/jobs/{id}/revise`, using feature/bugfix pipelines against a copied existing workspace.
- Added checkpoint approval continuation through `POST /api/jobs/{id}/approve` and web UI approval cards.
- Added failed-stage retry through `POST /api/jobs/{id}/retry` and ErrorPanel/JobPanel actions.
- Added artifact listing and a Vue artifact browser on top of the existing artifact payload API.
- Connected the workspace tree/file APIs to a Vue code browser.
- Connected revision requests, static preview builds, and approval actions in the JobPanel.
- Added optional low-cost LLM routing with structured JSON output, confidence fallback, and local keyword fallback.
- Added optional independent LLM review for implementation/verification/fix/regression stages.
- Added response-format pass-through from Python to the bridge for single-shot and agent paths.
- Added agent loop guards for repeated identical tool calls, tool timeout propagation, and usage-budget failure codes.
- Added failure evidence tails and implicated-file extraction into repair failure packets.
- Rebuilt the packaged Vue static assets for v6.

## Compatibility notes

- `APPFORGE_LLM_ROUTER=0` disables the optional LLM classifier and uses the local keyword router only.
- `APPFORGE_DISABLE_INDEPENDENT_REVIEW=1` disables the optional independent LLM review pass.
- The web default driver remains `llm-bridge-agent`; CLI `auto` now also resolves to the agent driver.
- The bundled static preview iframe still omits `allow-same-origin`.

## Validation run in this package

```bash
PYTHONPATH=. pytest -q \
  tests/test_json_extract.py \
  tests/test_pipelines.py \
  tests/test_projects_and_checkpoints.py \
  tests/test_prompting.py \
  tests/test_tools.py \
  tests/test_runner.py \
  tests/test_web.py \
  tests/test_llm_bridge.py
# 56 passed

npm --prefix frontend run build
# vue-tsc --noEmit && vite build completed
```

A full `pytest -q` collection still requires the optional `questionary` dependency for `tests/test_llm_auth.py` in this container.
