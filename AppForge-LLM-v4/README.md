# AppForge-LLM v4

**A Vite + Vue web app for describing an application once, watching every production stage, and downloading the verified source ZIP.**

[한국어 문서](README.ko.md) · [V4 engineering](docs/V4_ENGINEERING.md) · [Web app guide](docs/WEB_APP.md) · [Architecture](docs/ARCHITECTURE.md) · [Safety](docs/SAFETY.md) · [Agent Guide](AGENT_GUIDE.md)

AppForge-LLM v4 builds on the v3 Vite + Vue web UX with a hardened **Specification → Workflow → Memory → Loop Engineering** spine. Automatic pipeline routing, isolated project setup, coding-agent invocation, artifact validation, test/build/security gates, release checks, safe archive creation, and verified ZIP download remain server-owned. The v4 change is that an app is no longer pushed directly from intent to implementation: specification, flow, state memory, and feedback loops are each captured as validated contracts before downstream code work.

```text
Describe the app → Build → Follow live stage status → Download the completed source ZIP
```

## What changed in v4

- **Four-layer engineering spine:** major pipelines now enforce or strengthen `specification → workflow_design → memory_engineering → loop_engineering` before implementation.
- **Stronger artifact contracts:** `requirements_spec` and `workflow_spec` are stricter, and new `memory_spec` and `loop_spec` schemas make state and feedback loops explicit.
- **Persistent engineering memory:** the runner records stage outcomes, validation evidence, decisions, and failures in `.appforge/memory/stage-memory.jsonl` and summarizes them into later stage prompts.
- **Retry-loop guard:** repeated failure signatures are detected as `REPEATED_FAILURE_LOOP` so the runner stops unproductive automatic retries and asks for a changed strategy.
- **Web status updates:** the Korean timeline now names Memory/Loop engineering stages and explains repeated-loop failures.
- **External LLM only:** the web app proxies provider settings through FastAPI to a local Bun bridge and runs against a configured external LLM API key. Codex/Claude CLI fallback is not used.

## Fastest start

Prerequisites: Python 3.11+, Bun, and at least one external LLM API key configured in the web UI or exposed through a supported provider environment variable.

From a built wheel:

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install dist/openappforge-0.4.0-py3-none-any.whl

appforge web
```

From source, the easiest path is the launcher. It prepares the local Python
environment, refreshes the packaged Vue UI, starts the loopback web server, and
opens the browser:

```bash
./build.sh
```

When `uv sync` is supported and usable, the launcher uses it. Older `uv`
installations or local environments where `uv sync` cannot run fall back to the
same `.venv` plus a pip editable install path.

For prepared workspaces or CI-style local checks, run the no-browser smoke path:

```bash
APPFORGE_SKIP_INSTALL=1 APPFORGE_SKIP_FRONTEND_BUILD=1 ./build.sh --smoke
```

Manual source setup remains available:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm --prefix frontend install
npm --prefix frontend run build

appforge web
```

The default browser opens `http://127.0.0.1:8787`. If that port is already in use, `build.sh` scans upward and opens the next available port. The browser stores only the current job ID; authoritative state remains under `.appforge-web/jobs/`.

Useful launcher options:

```bash
./build.sh --no-open
APPFORGE_WEB_PORT=8799 ./build.sh
APPFORGE_SKIP_INSTALL=1 APPFORGE_SKIP_FRONTEND_BUILD=1 ./build.sh --check
```

The launcher keeps the existing `appforge web` command as the runtime authority.
By default it reuses or starts the local Bun bridge when available; set
`APPFORGE_SKIP_LLM_BRIDGE=1` to skip launcher-owned bridge startup.

## Frontend development

Run the Python API server and Vite dev server in two terminals:

```bash
appforge web --no-open-browser
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to the local FastAPI server at `http://127.0.0.1:8787`.

Build the production frontend into the Python package resources:

```bash
npm --prefix frontend run build
```

This writes `appforge/resources/web/index.html`, `appforge/resources/web/assets/*`, `favicon.svg`, and `manifest.webmanifest`.

## External LLM setup

Start the bridge in a separate terminal, then start the web app:

```bash
cd llm_bridge
bun install
bun run start
```

```bash
appforge web
```

The source launcher can perform the same bridge startup check:

```bash
./build.sh
```

When no bridge is already healthy at `APPFORGE_LLM_BRIDGE_URL`, the launcher
starts `llm_bridge` with Bun and writes its output to
`.appforge-web/llm-bridge.log`. If you customize the bridge URL, also set the
matching `APPFORGE_LLM_BRIDGE_HOST` and `APPFORGE_LLM_BRIDGE_PORT` values used
by the Bun service.

Open Provider Settings in the web UI to save an API key, choose a default model, test the provider, and activate it for AppForge. The browser talks only to the FastAPI `/api/llm` proxy; the bridge listens on `http://127.0.0.1:8788` by default. Provider keys are stored in the local bridge config path, or read from provider environment variables such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`, `OPENROUTER_API_KEY`, and `XAI_API_KEY`.

The `llm-bridge` driver is the default runtime path. `APPFORGE_DRIVER=auto` is treated as an alias for the same bridge path. Other driver values, including `codex`, `claude`, and `generic`, are rejected.

The bridge service is included in the source checkout and AppForge source archive. A Python wheel installation serves the built web UI and bridge proxy, but still needs access to the `llm_bridge/` service source or an already-running bridge at `APPFORGE_LLM_BRIDGE_URL`.

### Main environment variables

```text
APPFORGE_PROJECTS_DIR          generated project directory; default projects/
APPFORGE_DATA_DIR              persisted web-job state; default .appforge-web/
APPFORGE_DRIVER                llm-bridge by default; auto is an alias
APPFORGE_WEB_HOST              build.sh/appforge web bind host; default 127.0.0.1
APPFORGE_WEB_PORT              build.sh/appforge web preferred port; default 8787
APPFORGE_WEB_PORT_FALLBACK_LIMIT  additional web ports to scan upward; default 20
APPFORGE_NO_OPEN               build.sh no-browser foreground mode
APPFORGE_SKIP_INSTALL          build.sh reuses the existing .venv when true
APPFORGE_SKIP_FRONTEND_BUILD   build.sh reuses packaged web assets when true
APPFORGE_START_LLM_BRIDGE      build.sh starts or reuses the local bridge when true
APPFORGE_SKIP_LLM_BRIDGE       build.sh avoids launcher-owned bridge startup
APPFORGE_SMOKE_TIMEOUT         build.sh smoke timeout in seconds; default 30
APPFORGE_MODEL                 model passed to the selected driver
APPFORGE_LLM_BRIDGE_URL        llm-bridge URL; default http://127.0.0.1:8788
APPFORGE_LLM_PROVIDER          optional provider override for llm-bridge driver
APPFORGE_ALLOW_NETWORK         default true for installs and remote audits
APPFORGE_STAGE_TIMEOUT         per-stage timeout in seconds; default 3600
APPFORGE_MAX_STAGE_ATTEMPTS    optional maximum automatic attempts per stage
APPFORGE_MAX_TURNS             reserved for compatibility; ignored by the default LLM bridge
APPFORGE_UNSAFE_AGENT          reserved for compatibility; ignored by the default LLM bridge
APPFORGE_PROMPT_MAX_CHARS      request input limit; default 20000
```

Bridge-specific runtime variables:

```text
APPFORGE_LLM_BRIDGE_HOST       bridge bind host; default 127.0.0.1
APPFORGE_LLM_BRIDGE_PORT       bridge port; default 8788
APPFORGE_LLM_CONFIG_DIR        provider config directory; default ~/.appforge/llm
APPFORGE_LLM_CONFIG            provider config file path; default providers.json in config dir
```

## Existing CLI compatibility

`appforge web` is the recommended v4 experience, while the full CLI and repository-native agent loop remain available:

```bash
appforge forge "Build a responsive personal budget app" --driver llm-bridge --allow-network
appforge run <project>
appforge status <project>
appforge prompt <project>
appforge complete <project>
appforge doctor
appforge tool list
```

Existing-repository changes remain available through the CLI:

```bash
cd existing-project
appforge forge "Add passkey login while preserving password login" \
  --target . --driver llm-bridge --allow-network
```

## Development and verification

```bash
python -m pip install -e '.[dev]'
npm --prefix frontend install
npm --prefix frontend run build
cd llm_bridge && bun install && bun run typecheck && cd ..
python -m compileall -q appforge tests
python -m pytest
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

## License and inspiration

AppForge-LLM v4/OpenAppForge is original software released under the Apache License 2.0. Its repository-native combination of declarative pipelines, composable skills, auto-discovered tools, checkpoints, and review gates was inspired by OpenMontage. No OpenMontage source code is included. See [ATTRIBUTION.md](ATTRIBUTION.md).
