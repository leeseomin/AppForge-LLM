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
- **Local LLM bridge:** the web app can proxy provider settings through FastAPI to a local Bun bridge and run `APPFORGE_DRIVER=llm-bridge` against a configured external LLM provider.

## Fastest start

Prerequisites: Python 3.11+ and one of the following coding-agent executables:

- Codex CLI
- Claude Code CLI
- a custom coding-agent command configured through `APPFORGE_AGENT_CMD`
- Bun, when using the local `llm-bridge` provider path

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

The default browser opens `http://127.0.0.1:8787`. The browser stores only the current job ID; authoritative state remains under `.appforge-web/jobs/`.

Useful launcher options:

```bash
./build.sh --no-open
APPFORGE_WEB_PORT=8799 ./build.sh
APPFORGE_SKIP_INSTALL=1 APPFORGE_SKIP_FRONTEND_BUILD=1 ./build.sh --check
APPFORGE_DRIVER=llm-bridge ./build.sh
```

The launcher keeps the existing `appforge web` command as the runtime authority.
Set `APPFORGE_START_LLM_BRIDGE=1` or `APPFORGE_DRIVER=llm-bridge` to have it
reuse or start the local Bun bridge when available; set
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

## Coding-agent setup

### Automatic selection

The default `APPFORGE_DRIVER=auto` selects Codex CLI first and then Claude Code CLI. Readiness appears in the web app before the build action is enabled.

### Local LLM bridge

Start the bridge in a separate terminal, then start the web app with the bridge driver:

```bash
cd llm_bridge
bun install
bun run start
```

```bash
APPFORGE_DRIVER=llm-bridge appforge web
```

The source launcher can perform the same bridge startup check:

```bash
APPFORGE_DRIVER=llm-bridge ./build.sh
```

When no bridge is already healthy at `APPFORGE_LLM_BRIDGE_URL`, the launcher
starts `llm_bridge` with Bun and writes its output to
`.appforge-web/llm-bridge.log`. If you customize the bridge URL, also set the
matching `APPFORGE_LLM_BRIDGE_HOST` and `APPFORGE_LLM_BRIDGE_PORT` values used
by the Bun service.

Open Provider Settings in the web UI to save an API key, choose a default model, test the provider, and activate it for AppForge. The browser talks only to the FastAPI `/api/llm` proxy; the bridge listens on `http://127.0.0.1:8788` by default. Provider keys are stored in the local bridge config path, or read from provider environment variables such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`, `OPENROUTER_API_KEY`, and `XAI_API_KEY`.

The `llm-bridge` driver is a single-shot stage driver. It is useful for provider-backed stage generation, while fully agentic file-editing workflows should continue to use Codex, Claude Code, or a custom command driver.

The bridge service is included in the source checkout and AppForge source archive. A Python wheel installation serves the built web UI and bridge proxy, but still needs access to the `llm_bridge/` service source or an already-running bridge at `APPFORGE_LLM_BRIDGE_URL`.

### Custom agent command

```bash
APPFORGE_DRIVER=generic \
APPFORGE_AGENT_CMD='my-agent --workspace {workspace} --prompt {prompt_file}' \
appforge web
```

Available placeholders are `{workspace}`, `{prompt_file}`, `{result_file}`, `{stage}`, and `{attempt}`. When `{prompt_file}` is omitted, the full stage packet is sent on standard input.

### Main environment variables

```text
APPFORGE_PROJECTS_DIR          generated project directory; default projects/
APPFORGE_DATA_DIR              persisted web-job state; default .appforge-web/
APPFORGE_DRIVER                auto | codex | claude | generic | llm-bridge
APPFORGE_WEB_HOST              build.sh/appforge web bind host; default 127.0.0.1
APPFORGE_WEB_PORT              build.sh/appforge web port; default 8787
APPFORGE_NO_OPEN               build.sh no-browser foreground mode
APPFORGE_SKIP_INSTALL          build.sh reuses the existing .venv when true
APPFORGE_SKIP_FRONTEND_BUILD   build.sh reuses packaged web assets when true
APPFORGE_START_LLM_BRIDGE      build.sh starts or reuses the local bridge when true
APPFORGE_SKIP_LLM_BRIDGE       build.sh avoids launcher-owned bridge startup
APPFORGE_SMOKE_TIMEOUT         build.sh smoke timeout in seconds; default 30
APPFORGE_AGENT_CMD             generic command template
APPFORGE_MODEL                 model passed to the selected driver
APPFORGE_LLM_BRIDGE_URL        llm-bridge URL; default http://127.0.0.1:8788
APPFORGE_LLM_PROVIDER          optional provider override for llm-bridge driver
APPFORGE_ALLOW_NETWORK         default true for installs and remote audits
APPFORGE_STAGE_TIMEOUT         per-stage timeout in seconds; default 3600
APPFORGE_MAX_STAGE_ATTEMPTS    optional maximum automatic attempts per stage
APPFORGE_MAX_TURNS             optional Claude Code turn limit
APPFORGE_UNSAFE_AGENT          default false; isolated environments only
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
appforge forge "Build a responsive personal budget app" --driver auto --allow-network
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
  --target . --driver auto --allow-network
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
