# AppForge-LLM v7

**A local web app that turns one prompt into a planned, tested, previewable source project.**

[한국어 문서](README.ko.md) · [Web guide](docs/WEB_APP.md) · [Architecture](docs/ARCHITECTURE.md) · [Safety](docs/SAFETY.md) · [Changelog](CHANGELOG.md)

The browser is the primary product surface. AppForge routes an app request through a controlled build pipeline, streams progress, exposes the generated code and artifacts for review, builds a local preview, and enables a source ZIP only after validation succeeds.

```text
Connect an LLM → Describe the app → Build and verify → Inspect or revise → Download source
```

## What the web app does

- Connects to supported LLM providers with an API key or supported OAuth login.
- Runs requests in fully autonomous or checkpoint approval mode.
- Queues work while one server-owned job executes at a time.
- Streams stages, model activity, tool calls, retries, and failures to the UI.
- Lets you inspect generated files and machine-validated intermediate artifacts.
- Builds a sandboxed local preview when the generated stack supports it.
- Supports stage retry, approval, cancellation, and follow-up revision jobs.
- Produces a source ZIP only after the pipeline and archive checks pass.

AppForge does not automatically deploy the generated app or mutate production data.

## How it works

```text
Vue browser UI
      │ session-token API + SSE
      ▼
FastAPI web server
      ├─ JobManager: queue, durable job state, preview, ZIP
      ├─ PipelineRunner: manifests, tools, schemas, gates, checkpoints
      └─ LLM proxy
              ▼
        Bun llm_bridge
              ▼
       External LLM provider
```

The browser is never the source of truth for completion. The server creates an isolated workspace under `projects/`, executes declared tools and validation gates, persists checkpoints under the project’s `.appforge/` directory, and exposes download only after the resulting archive is validated.

## Quick start

Requirements:

- Python 3.11+
- Node.js and npm
- Bun
- An external LLM account or API key

From the repository root:

```bash
./build.sh
```

The launcher prepares the Python environment, installs/builds the Vue frontend, starts or reuses the local LLM bridge, starts FastAPI, and opens the browser. First launch may download dependencies.

The web UI defaults to `http://127.0.0.1:8787`; the bridge defaults to `http://127.0.0.1:8788`. Open **LLM 연결**, configure and test a provider, choose a model, then enter the app you want to build.

Useful launcher commands:

```bash
./build.sh --no-open
./build.sh --check
APPFORGE_SKIP_INSTALL=1 APPFORGE_SKIP_FRONTEND_BUILD=1 ./build.sh --smoke
```

## Local state and safety

| State | Authoritative location |
|---|---|
| Generated projects | `projects/` |
| Web job state and logs | `.appforge-web/` |
| Pipeline artifacts and checkpoints | `projects/<project>/.appforge/` |
| Provider configuration | `~/.appforge/llm/providers.json` by default |
| Current browser job | job ID in `localStorage` |
| Web session credential | token in `sessionStorage` |

- The web server and bridge bind to loopback by default.
- Protected API requests require a per-process session token and local host/origin checks.
- Provider secrets are not returned to the browser. File-backed secrets use `0600`; macOS Keychain is optional.
- Network-capable and destructive AppForge tools are disabled by default.
- Generated previews run with a restrictive sandbox and content security policy.

Important configuration:

```text
APPFORGE_WEB_PORT             web port; default 8787
APPFORGE_PROJECTS_DIR         generated workspaces; default projects/
APPFORGE_DATA_DIR             durable web jobs; default .appforge-web/
APPFORGE_LLM_BRIDGE_URL       bridge URL; default http://127.0.0.1:8788
APPFORGE_ALLOW_NETWORK        allow dependency/network tools; default false
APPFORGE_ALLOW_DESTRUCTIVE    allow destructive AppForge tools; default false
APPFORGE_LLM_SECRET_BACKEND   file or macOS keychain; default file
```

See [docs/WEB_APP.md](docs/WEB_APP.md) for the complete configuration and API behavior.

## Development

Install development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm --prefix frontend install
(cd llm_bridge && bun install)
```

For frontend development, run the API and Vite in separate terminals:

```bash
appforge web --no-open-browser
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to FastAPI.

Verify the repository:

```bash
make check
(cd llm_bridge && bun run typecheck && bun test)
```

The production frontend is built from `frontend/` into `appforge/resources/web/` and packaged with the Python application.

## License

Apache-2.0. See [LICENSE](LICENSE) and [ATTRIBUTION.md](ATTRIBUTION.md).
