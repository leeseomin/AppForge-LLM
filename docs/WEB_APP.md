# AppForge-LLM v7 Web Application

The v7 web application is the default browser experience for autonomous AI app generation. It lets a user describe an app once, then follows a controlled agent pipeline through planning, implementation, validation, preview, and source ZIP packaging.

## Start

```bash
python -m pip install dist/openappforge-0.7.0-py3-none-any.whl
appforge web
```

For a source checkout, use the launcher first:

```bash
./build.sh
```

It syncs the Python environment, installs frontend packages when needed, builds
the packaged Vue assets, and delegates to `appforge web`. When `uv sync` is not
available or cannot be used locally, it falls back to `.venv` plus
`pip install -e '.[dev]'`. Normal mode opens the default browser at
`http://127.0.0.1:8787`.

Useful source-launcher checks and variants:

```bash
APPFORGE_SKIP_INSTALL=1 APPFORGE_SKIP_FRONTEND_BUILD=1 ./build.sh --smoke
./build.sh --no-open
APPFORGE_WEB_PORT=8799 ./build.sh
APPFORGE_SKIP_INSTALL=1 APPFORGE_SKIP_FRONTEND_BUILD=1 ./build.sh --check
```

Manual source development remains available:

```bash
python -m pip install -e '.[dev]'
npm --prefix frontend install
npm --prefix frontend run build
appforge web
```

Managed bridge startup through `appforge web` is recommended because it creates
a high-entropy capability in memory and gives the child process only an
allowlisted environment. To manage the bridge separately, set the same secret,
at least 32 characters long, in both process environments without putting its
value in a URL, command argument, or log:

```bash
cd llm_bridge
bun install
test "${#APPFORGE_LLM_BRIDGE_TOKEN}" -ge 32
bun run start
```

Then run the web app with:

```bash
APPFORGE_DRIVER=llm-bridge-agent appforge web
```

The source launcher can also reuse or start the bridge when bridge mode is
selected:

```bash
APPFORGE_DRIVER=llm-bridge-agent ./build.sh
```

Set `APPFORGE_START_LLM_BRIDGE=1` to request secure web-process bridge startup
without switching drivers, or `APPFORGE_SKIP_LLM_BRIDGE=1` to leave bridge
management to another terminal. The `appforge web` server has on-demand
loopback bridge startup controlled by `APPFORGE_LLM_BRIDGE_AUTOSTART`, which
defaults to true. Managed bridge logs are written to
`.appforge-web/llm-bridge.log` with owner-only permissions.

Equivalent standalone entry point:

```bash
appforge-web
```

Useful server-only flags:

```bash
appforge web --host 127.0.0.1 --port 8787 --no-open-browser
```

The web process accepts only `127.0.0.1`, `localhost`, or `::1`; a request to bind `0.0.0.0` or another shared interface is rejected. Use a separate, explicitly authenticated proxy architecture if remote access is ever designed and reviewed.

## Frontend architecture

```text
frontend/
├── index.html
├── vite.config.ts
├── package.json
└── src/
    ├── App.vue
    ├── api.ts
    ├── types.ts
    ├── styles.css
    └── components/
```

Development server:

```bash
appforge web --no-open-browser
npm --prefix frontend run dev
```

Vite serves the UI at `http://127.0.0.1:5173` and proxies `/api` to `http://127.0.0.1:8787`. Production builds are emitted to `appforge/resources/web/`, where FastAPI serves them without Node.

## User journey

1. The page checks coding-agent readiness through `GET /api/health`.
2. When `APPFORGE_DRIVER=llm-bridge`, the user can open Provider Settings, save a provider key, test it, choose a default model, and activate it through the same-origin `/api/llm` proxy.
3. The user enters one natural-language application request.
4. `POST /api/jobs` automatically selects the pipeline and starts one background job.
5. The Vue app polls `GET /api/jobs/{id}` and renders every system and pipeline stage.
6. The selected pipeline includes explicit Specification, Workflow, Memory, and Loop engineering stages before implementation-oriented work.
7. Failed attempts appear as `retrying` while attempts remain.
8. A terminal failure includes a stable code, action, stage, attempt, agent result, failed checks, and critical review findings.
9. On success, the server validates the ZIP and sets `download.available=true`.
10. `GET /api/jobs/{id}/download` serves only that job's verified archive.

The UI stores only the active job ID in browser local storage. Authoritative job state is persisted in `.appforge-web/jobs/<job-id>.json`.

## v7 bridge continuity

Generation and agent-event requests disable Bun's per-request idle timer and emit an SSE heartbeat every five seconds by default. The Python client treats EOF without `done`, `error`, or `cancelled` as an interrupted stream rather than a successful completion. Transient connection failures are retried within the original stage deadline, and agent stages continue from the existing workspace and already validated artifacts. A recovery attempt appears as `bridge_recovering` in the job event stream and as `retrying` in the stage timeline.

## Job statuses

```text
queued → initializing → running → packaging → completed
                                      └────────→ failed
```

Stage statuses are:

```text
pending | running | validating | retrying | completed | failed
```

The overall percentage is derived from all visible system and pipeline stages. It reaches 100 only after archive validation succeeds.

## API

### `GET /api/health`

Returns server version, driver readiness, active-job ID, network policy, prompt limit, and safety policy. The UI disables the start action until a supported coding-agent executable is available.

With `APPFORGE_DRIVER=llm-bridge`, readiness requires the bridge to be reachable and the active provider/model selection to be configured.

### `/api/llm`

The web server proxies provider operations to the local bridge so the browser stays on the FastAPI origin:

- `GET /api/llm/health`
- `GET /api/llm/providers`
- `GET /api/llm/providers/{provider_id}/models`
- `PUT /api/llm/providers/{provider_id}`
- `DELETE /api/llm/providers/{provider_id}`
- `POST /api/llm/providers/{provider_id}/test`
- `GET /api/llm/active`
- `PUT /api/llm/active`

### `POST /api/jobs`

Request:

```json
{
  "prompt": "Build a responsive local-first budget web app with tests."
}
```

Returns HTTP 202 and the complete public job snapshot. Only one job may be active; a second request returns HTTP 409 with `error.context.current_job_id` so the client can reconnect to the existing run.

### `GET /api/jobs/{job_id}`

Returns the current snapshot, including:

- pipeline and project information;
- ordered stage records;
- progress and active stage;
- compact event history;
- structured terminal error;
- download availability, URL, filename, and size.

### `GET /api/jobs/{job_id}/download`

Returns HTTP 409 until the job completes. The path is resolved from server-owned job state and must remain under the project's `.appforge/reports/` directory.

## Static serving

- `/` serves `appforge/resources/web/index.html`.
- `/assets/*` serves Vite hashed assets with immutable cache headers.
- `/favicon.svg` and `/manifest.webmanifest` are served from the built frontend output.
- Unknown non-API routes fall back to the SPA entry so browser refreshes keep working.
- Unknown `/api/*` and missing `/assets/*` paths return 404 rather than the SPA shell.
- The repository keeps the packaged web bundle for source checkout convenience; `make web-bundle-check` rebuilds it and fails if `appforge/resources/web` drifts from `frontend/`.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `APPFORGE_PROJECTS_DIR` | `projects` | Generated workspaces |
| `APPFORGE_DATA_DIR` | `.appforge-web` | Persisted web-job state |
| `APPFORGE_DRIVER` | `llm-bridge-agent` | `llm-bridge-agent`, `llm-bridge`, or `auto` (same bridge-backed path); `codex`/`claude`/`generic` are rejected |
| `APPFORGE_WEB_HOST` | `127.0.0.1` | `build.sh`/`appforge web` bind host |
| `APPFORGE_WEB_PORT` | `8787` | `build.sh`/`appforge web` bind port |
| `APPFORGE_NO_OPEN` | `false` | Suppress browser opening in `build.sh` normal mode |
| `APPFORGE_SKIP_INSTALL` | `false` | Reuse the existing `.venv` in `build.sh` |
| `APPFORGE_SKIP_FRONTEND_BUILD` | `false` | Reuse current packaged web assets in `build.sh` |
| `APPFORGE_START_LLM_BRIDGE` | `false` | Request launcher-owned bridge startup or reuse |
| `APPFORGE_SKIP_LLM_BRIDGE` | `false` | Disable launcher-owned bridge startup |
| `APPFORGE_LLM_BRIDGE_AUTOSTART` | `true` | Allow `appforge web` to start or reuse a local loopback bridge on demand |
| `APPFORGE_SMOKE_TIMEOUT` | `30` | Seconds for `./build.sh --smoke` health/UI probes |
| `APPFORGE_BRIDGE_TIMEOUT` | `15` | Seconds for launcher-owned bridge health checks |
| `APPFORGE_AGENT_CMD` | unset | Removed (generic command driver is no longer supported) |
| `APPFORGE_MODEL` | unset | Model passed to the llm-bridge drivers |
| `APPFORGE_LLM_BRIDGE_URL` | `http://127.0.0.1:8788` | FastAPI-to-bridge URL |
| `APPFORGE_LLM_BRIDGE_TOKEN` | generated in memory | Required shared capability for a manually managed bridge; must be at least 32 characters |
| `APPFORGE_LLM_BRIDGE_IDLE_TIMEOUT` | `30` | Bun idle timeout in seconds for ordinary bridge routes; long generation/stream routes disable it per request |
| `APPFORGE_LLM_BRIDGE_HEARTBEAT_MS` | `5000` | SSE heartbeat interval, clamped to 1000–60000 ms |
| `APPFORGE_LLM_PROVIDER` | unset | Optional provider override for the llm-bridge drivers |
| `APPFORGE_LLM_SECRET_BACKEND` | macOS `keychain`; otherwise `file` | Bridge secret storage backend: `file` or macOS `keychain` |
| `APPFORGE_ALLOW_NETWORK` | `false` | Dependency downloads and remote audits; enable explicitly when needed |
| `APPFORGE_ALLOW_DESTRUCTIVE` | `false` | Destructive AppForge tools; keep disabled for normal web use |
| `APPFORGE_UNSAFE_AGENT` | `false` | Agent sandbox bypass; isolated environments only |
| `APPFORGE_STAGE_TIMEOUT` | `3600` | Per-stage timeout in seconds |
| `APPFORGE_MAX_STAGE_ATTEMPTS` | pipeline default | Override automatic attempts |
| `APPFORGE_MAX_TURNS` | unset | Optional tool-call turn limit for `llm-bridge-agent` |
| `APPFORGE_PROMPT_MAX_CHARS` | `20000` | Request input limit |

Boolean values accept `true/false`, `1/0`, `yes/no`, or `on/off`.

The bridge process also accepts `APPFORGE_LLM_BRIDGE_HOST`, `APPFORGE_LLM_BRIDGE_PORT`, `APPFORGE_LLM_BRIDGE_TOKEN`, `APPFORGE_LLM_CONFIG_DIR`, `APPFORGE_LLM_CONFIG`, and `APPFORGE_LLM_SECRET_BACKEND`. Provider keys may be stored through the UI or supplied through provider-specific variables such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`, `OPENROUTER_API_KEY`, and `XAI_API_KEY`. macOS defaults to Keychain and stores only `keychain:` references in JSON. Other platforms default to a local file; its directory is owner-only (`0700`), and the bridge rejects unsafe types/symlinks and atomically replaces owner-only (`0600`) files. Existing macOS plaintext file secrets are migrated to Keychain on load.

## Session shutdown

The **세션 종료** control cancels the active web job, aborts any in-flight
`/generate` request from the Python server to the LLM bridge, schedules the
FastAPI process to stop, immediately rotates the server credential and clears
the browser cookie, and shuts down only a launcher/app-owned bridge process.
If `APPFORGE_LLM_BRIDGE_URL` points at a bridge that was already running outside
this app, that bridge process remains running; its current generation request is
still cancelled through the dropped HTTP connection.

## Error contract

API errors use:

```json
{
  "error": {
    "code": "STAGE_CHECK_FAILED",
    "title": "필수 검증을 통과하지 못했습니다",
    "message": "Required validation failed in stage verification: run_tests",
    "action": "Fix the failed check and retry.",
    "stage": "verification",
    "attempt": 2,
    "technical": {
      "driver": {},
      "failed_checks": [],
      "review_findings": []
    }
  }
}
```

Captured agent output is passed through the framework's secret redaction and size limits before it reaches web-job state or the browser.

## Persistence and restart behavior

Job snapshots are written atomically after meaningful state changes. A process restart cannot resume a subprocess that was already executing, so any persisted `queued`, `initializing`, `running`, or `packaging` job is converted to a terminal `SERVER_RESTARTED` failure during startup. The generated project and `.appforge/` checkpoints remain available for CLI inspection or a new web run.

Completed jobs remain downloadable while their archive exists. If the archive is moved or deleted, the persisted record becomes an `ARCHIVE_MISSING` failure on the next server startup.

## Security boundaries

- Default binding is `127.0.0.1`.
- No CORS policy is enabled.
- Requests with non-loopback `Host` headers are rejected to reduce DNS rebinding risk.
- `/api/health` remains public for local readiness checks. The browser launch URL contains a one-time code only in its fragment; the Vue client clears the fragment before exchanging the code for an `HttpOnly; SameSite=Strict` session cookie.
- Other `/api/*` routes require the session cookie. Credentials are not stored in Web Storage or accepted in URL queries; same-origin cookies also cover SSE and ZIP downloads.
- Cross-site fetch metadata is rejected, and unsafe methods require an `Origin` or `Referer` whose scheme, host, and port exactly match the web request.
- The browser uses FastAPI `/api/llm` routes for provider settings; direct browser-to-bridge CORS access is not required.
- The bridge is loopback-only. Its minimal `/health` route is public; every other route requires `X-AppForge-Bridge-Token`, uses no browser CORS, and rejects cross-origin requests.
- Managed bridge startup generates that capability in memory, forwards only an allowlisted environment, and does not expose provider keys to generated-project processes.
- Generated-project commands run with a scrubbed environment and disposable home inside the supported operating-system sandbox; command execution fails closed when confinement is unavailable.
- API and page responses use `Cache-Control: no-store`.
- Responses include restrictive CSP, frame, referrer, MIME, and permissions headers.
- Generated app previews are served with CSP `sandbox`, no same-origin iframe permission, and `connect-src 'none'` for direct `/preview/` access.
- The Vue client renders dynamic text through normal template escaping, not HTML injection.
- Download paths are never accepted from clients.
- The archive tool excludes secrets, VCS state, dependencies, caches, and `.appforge/` runtime files.
- The required secret-scan gate and archive-time scan both fail closed; matched values are redacted, and the archive rescans the exact bytes it writes.
- Provider API keys are never echoed back by bridge responses. The provider list reports only key presence and source; omitted keys remain unchanged unless an explicit clear operation is requested.
- Built-in provider endpoints and credential-variable names come from the local registry. A remote model catalog may update display/model metadata but cannot redirect requests or choose an environment variable.
- The web workflow never enables deployment.
- One active job avoids ambiguous state and local resource contention.

The application and bridge do not provide multi-user authentication. Keep them loopback-only unless an external authenticated reverse proxy and operating-system isolation are in place.
