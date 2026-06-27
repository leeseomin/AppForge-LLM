# OpenAppForge

An autonomous AI agent service that builds apps on its own with just a prompt.
(프롬프트 하나만으로 알아서 앱을 구축하는 자율형 AI 에이전트 서비스)

**Turn a natural-language product command into a release-ready software source handoff.**

[한국어 문서](README.ko.md) · [Architecture](docs/ARCHITECTURE.md) · [Safety](docs/SAFETY.md) · [Agent Guide](AGENT_GUIDE.md)

OpenAppForge is a pipeline-driven production agent for AI coding assistants. It routes a request to a software pipeline, gives the coding agent a stage-specific operating packet, validates structured artifacts, executes deterministic quality gates, records durable checkpoints, retries failed stages, and packages the verified source.

It deliberately separates two responsibilities:

- **The coding agent** reasons, edits source, and resolves implementation problems.
- **OpenAppForge** supplies production policy, tools, artifact contracts, safety boundaries, review gates, and resumable state.

The default web runtime uses the local LLM bridge with external provider API keys. Repository-native assistants can still follow the agent guide manually when you want direct editor-side control.

## One-command use

Prerequisites: Python 3.11+, Bun, and at least one external LLM API key configured through the local LLM bridge.

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e .

appforge forge \
  "Build a responsive personal finance web app with local-first storage, CSV import, budgets, tests, and Docker support" \
  --driver llm-bridge \
  --allow-network
```

`forge` performs routing, initialization, stage execution, validation, retry, review, checkpointing, and source packaging. Network access is opt-in. Deployment, publishing, Git push, paid-resource creation, and production-data changes are never authorized by this command.

The output is created under `projects/<slug>/`. Progress and evidence live in its `.appforge/` directory; the final source archive is written under `.appforge/reports/`.

## Existing repository

```bash
cd existing-project
appforge forge "Add passkey login while preserving password login" --target . --driver llm-bridge --allow-network
```

A request containing bug/fix language is routed to the bugfix pipeline; other existing-repository work defaults to the feature pipeline. Baseline behavior, unrelated working-tree changes, and regression evidence are part of the stage contract.

## Agent-native mode

Open this repository in a coding assistant and issue a command such as:

```text
Use AGENT_GUIDE.md. Build a small inventory web app with barcode search,
role-based access, tests, a Docker setup, and a release-ready source archive.
Continue through every pipeline stage and do not stop after planning.
```

The assistant uses these commands internally:

```bash
appforge new "<request>" --pipeline auto --mode autonomous
appforge prompt <project> --output <project>/.appforge/current-stage.md
appforge complete <project> --auto-approve
appforge status <project>
```

Adapters are included for `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, Gemini, GitHub Copilot, Cursor, and Windsurf.

## Production model

```text
natural-language command
        │
        ▼
automatic pipeline router
        │
        ▼
YAML stage manifest ──► stage skill + prior artifacts + repository context
        │                                      │
        │                                      ▼
        │                               external LLM bridge
        │                                      │
        ▼                                      ▼
JSON artifact contracts ◄──────────── source edits + stage-result.json
        │
        ▼
deterministic gates: tests · lint · typecheck · build · secrets · readiness
        │
        ▼
review + checkpoint + retry/resume
        │
        ▼
release-ready source archive and handoff report
```

The framework ships with:

- **12 pipelines:** web app, full-stack SaaS, API service, CLI, desktop, mobile, data app, automation, library/SDK, prototype, feature, and bugfix.
- **55+ composable skills:** stage playbooks, stack guidance, domain guidance, failure recovery, review, safety, and definition of done.
- **20+ auditable tools:** repository inspection, bounded file operations, command execution, stack detection, tests, lint, type checking, build, secret scanning, dependency audit, SBOM, release readiness, and safe archiving.
- **22 JSON artifact schemas:** product, requirements, architecture, UX, API/data contracts, implementation, verification, security, operations, release, handoff, bug diagnosis, regression, and related evidence.

## Essential commands

```bash
appforge pipelines                         # list available production paths
appforge route "Build a Flutter app"       # preview routing scores
appforge new "Build a CLI"                 # initialize without running
appforge run projects/build-a-cli          # resume through the external LLM bridge
appforge status projects/build-a-cli       # show durable checkpoint state
appforge prompt projects/build-a-cli       # render next stage packet
appforge complete projects/build-a-cli     # validate manually completed work
appforge preflight projects/build-a-cli    # inspect stack and quality commands
appforge doctor                            # inspect the LLM bridge and tool support
appforge tool list                         # list live tool contracts
```

## Durable project state

```text
.appforge/
├── project.json          # request, pipeline, mode, and safety policy
├── state.json            # current/completed stages
├── stage-result.json     # fresh completion record from the agent
├── artifacts/            # schema-validated product and engineering evidence
├── checkpoints/          # one atomic record per stage
├── prompts/              # exact stage packets used by the runner
├── logs/                 # driver output and attempt records
└── reports/              # SBOM, inventories, run evidence, source archive
```

A stopped run can be resumed with `appforge run <project>`. Completed stages are not repeated; failed stages receive the previous review findings in their next packet. A fresh `stage-result.json` is required for every attempt, preventing a stale success record from validating new work.

## Safety and definition of done

By default, OpenAppForge:

- keeps edits inside the selected workspace;
- runs coding agents in their workspace-write/automatic permission mode rather than bypass mode;
- blocks network-dependent AppForge tools unless `--allow-network` is set;
- requires explicit opt-in for destructive tool actions;
- redacts likely secrets from captured command output;
- scans source for credentials before release;
- excludes `.env`, private keys, credentials, VCS data, dependencies, caches, and `.appforge/` internals from the source archive;
- prepares deployment instructions but does not deploy or publish.

The network flag is a tool-policy control, not an OS firewall. Tests and build scripts execute repository code; isolate untrusted work in a container or VM.

“Complete” means implemented behavior, executed verification evidence, successful required gates, security review, reproducible quickstart/build guidance, and a packaged source handoff. It does not mean that external deployment has occurred.

See [docs/SAFETY.md](docs/SAFETY.md) for the trust model and [docs/EXTENDING.md](docs/EXTENDING.md) for adding pipelines, skills, schemas, tools, and drivers.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
python -m build
```

## License and inspiration

OpenAppForge is original software released under the Apache License 2.0. Its architectural approach was inspired by OpenMontage's repository-native combination of declarative pipelines, composable skills, auto-discovered tools, checkpoints, and review gates. No OpenMontage source code is included. See [ATTRIBUTION.md](ATTRIBUTION.md).
