# AppForge-LLM v4 Web Application

The v4 web application is the default browser experience for application generation. It uses a Vite + Vue frontend while preserving the FastAPI job API and adding the v4 Specification → Workflow → Memory → Loop engineering spine to backend production pipelines.

## Start

```bash
python -m pip install dist/openappforge-0.4.0-py3-none-any.whl
appforge web
```

For source development:

```bash
python -m pip install -e '.[dev]'
npm --prefix frontend install
npm --prefix frontend run build
appforge web
```

Equivalent standalone entry point:

```bash
appforge-web
```

Useful server-only flags:

```bash
appforge web --host 127.0.0.1 --port 8787 --no-open-browser
```

The default bind address is loopback-only. Binding to `0.0.0.0` exposes the interface to the surrounding network and should only be done behind appropriate host, authentication, and network controls.

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
2. The user enters one natural-language application request.
3. `POST /api/jobs` automatically selects the pipeline and starts one background job.
4. The Vue app polls `GET /api/jobs/{id}` and renders every system and pipeline stage.
5. The selected pipeline includes explicit Specification, Workflow, Memory, and Loop engineering stages before implementation-oriented work.
6. Failed attempts appear as `retrying` while attempts remain.
7. A terminal failure includes a stable code, action, stage, attempt, agent result, failed checks, and critical review findings.
8. On success, the server validates the ZIP and sets `download.available=true`.
9. `GET /api/jobs/{id}/download` serves only that job's verified archive.

The UI stores only the active job ID in browser local storage. Authoritative job state is persisted in `.appforge-web/jobs/<job-id>.json`.

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

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `APPFORGE_PROJECTS_DIR` | `projects` | Generated workspaces |
| `APPFORGE_DATA_DIR` | `.appforge-web` | Persisted web-job state |
| `APPFORGE_DRIVER` | `auto` | `auto`, `codex`, `claude`, or `generic` |
| `APPFORGE_AGENT_CMD` | unset | Generic command template |
| `APPFORGE_MODEL` | unset | Model passed to Codex/Claude driver |
| `APPFORGE_ALLOW_NETWORK` | `true` | Dependency downloads and remote audits |
| `APPFORGE_ALLOW_DESTRUCTIVE` | `false` | Destructive AppForge tools; keep disabled for normal web use |
| `APPFORGE_UNSAFE_AGENT` | `false` | Agent sandbox bypass; isolated environments only |
| `APPFORGE_STAGE_TIMEOUT` | `3600` | Per-stage timeout in seconds |
| `APPFORGE_MAX_STAGE_ATTEMPTS` | pipeline default | Override automatic attempts |
| `APPFORGE_MAX_TURNS` | unset | Claude Code turn limit |
| `APPFORGE_PROMPT_MAX_CHARS` | `20000` | Request input limit |

Boolean values accept `true/false`, `1/0`, `yes/no`, or `on/off`.

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
- API and page responses use `Cache-Control: no-store`.
- Responses include restrictive CSP, frame, referrer, MIME, and permissions headers.
- The Vue client renders dynamic text through normal template escaping, not HTML injection.
- Download paths are never accepted from clients.
- The archive tool excludes secrets, VCS state, dependencies, caches, and `.appforge/` runtime files.
- The web workflow never enables deployment.
- One active job avoids ambiguous state and local resource contention.

The application does not provide multi-user authentication. Keep it loopback-only unless an external authenticated reverse proxy and operating-system isolation are in place.
