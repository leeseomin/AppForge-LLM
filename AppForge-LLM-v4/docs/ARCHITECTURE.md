# Architecture

## Design objective

OpenAppForge must let a capable coding assistant finish software production rather than merely propose a plan. It therefore keeps model reasoning at the edge and makes process control deterministic, inspectable, and resumable.


## Web interaction layer

AppForge-LLM v4 adds a Vite + Vue local web layer above the unchanged production control plane. It does not replace pipeline policy or trust the browser to declare completion.

- `appforge.web` serves the built Vite + Vue single-page interface and narrow job API.
- `appforge.web_jobs.JobManager` allows one active job, persists public job snapshots, and maps runner events to user-facing stage states.
- `PipelineRunner` emits structured lifecycle events without depending on the web layer. Event-handler failures are isolated from production execution.
- The browser polls server-owned state; it stores only the current job ID locally.
- The download endpoint resolves an archive path from server-owned job state and exposes it only after pipeline success and ZIP integrity validation.

The visible web sequence includes two system steps before the selected pipeline (`preflight`, `project_setup`) and one system step after it (`download_package`). Pipeline stages remain the source of truth for production completion.

## Control plane

The control plane consists of six layers:

1. **Router** — scores the natural-language request against pipeline keywords and hard routing rules. Existing repositories receive feature/bugfix preference.
2. **Pipeline manifest** — a versioned YAML document that orders stages and declares required artifacts, candidate tools, review criteria, approvals, attempts, and executable gates.
3. **Stage packet compiler** — combines the stage skill, meta-skills, user request, prior validated artifacts, repository tree, detected stack/domain guidance, artifact schemas, tool contracts, and prior failure findings into one bounded prompt.
4. **Agent driver** — invokes Codex, Claude Code, or a generic local command in the selected workspace. The driver is not trusted to declare its own success.
5. **Engineering memory layer** — records compact stage outcomes, decisions, validation summaries, and repeated-failure signatures for later packets.
6. **Gate/checkpoint engine** — validates the completion record and artifact schemas, executes declared tools, performs deterministic review, and atomically records the result.

The next stage is calculated from completed checkpoint files, not from conversational memory. v4 also keeps a compact redacted engineering ledger under `.appforge/memory/` so later stage packets can inherit decisions, failures, and accepted contracts without trusting chat history.


## V4 engineering spine

Before implementation-oriented work, built-in pipelines now enforce or strengthen this contract sequence:

```text
specification → workflow_design → memory_engineering → loop_engineering
```

`requirements_spec` and `workflow_spec` remain machine-validated artifacts, but v4 makes them stricter. `memory_spec` captures durable/session/cache/audit/control-plane state and lifecycle behavior. `loop_spec` captures retry, polling, worker, reconciliation, validation, and human-review loops with budgets, convergence signals, and escape hatches.

## Data plane

A project owns a `.appforge/` control directory. JSON is used for durable machine-readable state; Markdown is used for human/agent operating knowledge; YAML is used for pipeline policy.

The primary invariant is:

```text
A stage is complete only when:
  driver succeeded
  AND a fresh completion record validates
  AND every required artifact validates
  AND every required executable gate passes
  AND deterministic review has zero critical findings
```

The runner deletes the previous `stage-result.json` at the beginning of every attempt. Artifacts can be refined in place, but a stale completion declaration cannot pass a later attempt. If a stage repeats the same failing signature, the loop guard returns `REPEATED_FAILURE_LOOP` to stop unproductive retries and force a strategy change.

## Why one coding agent instead of an internal agent swarm

Modern coding assistants already inspect files, edit code, run commands, and reason over failures. Recreating those capabilities behind another model API adds cost, duplicated context, and vendor coupling. OpenAppForge instead turns the assistant itself into the production worker and supplies the missing process discipline.

“Skills” are therefore operating documents, not hidden model calls. “Tools” are deterministic Python contracts. “Review” is based on schemas and executed evidence. This makes the same repository usable from several assistants.

## Pipeline anatomy

A manifest stage includes:

- `name`, `description`, and a Markdown `skill`;
- `produces`, the required artifact schema names;
- `tools`, which are made visible to the coding agent;
- `gates`, which the orchestrator actually executes;
- `review_focus` and `success_criteria`;
- `approval`, used in guided mode.

Tools listed under `tools` are affordances; tools under `gates` are enforcement. This distinction prevents documentation from being mistaken for evidence.

## Failure and recovery

Each stage has a bounded attempt count. A failed attempt writes:

- exact stage prompt;
- agent command and captured output;
- completion/artifact/gate records;
- deterministic review findings;
- failed checkpoint.

The next attempt receives the previous failure as structured context. Process interruption is recovered by scanning checkpoints. Guided approval is represented as `awaiting_human`, which is durable and can be approved separately.

## Extension boundaries

- Add policy or workflow variation as a pipeline manifest.
- Add reasoning guidance as a Markdown skill.
- Add structured evidence as a JSON schema.
- Add deterministic capability as a `Tool` subclass.
- Add a model/assistant integration as an `AgentDriver` subclass.

Keeping these concerns separate is the main maintainability constraint.
