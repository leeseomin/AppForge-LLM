# AppForge-LLM v5 Engineering Spine

v5 extends the production pipeline around four contracts that are generated before implementation work begins:

```text
Specification Engineering
↓
Workflow Engineering
↓
Memory Engineering
↓
Loop Engineering
```

## 1. Specification Engineering

The `specification` stage produces `.appforge/artifacts/requirements_spec.json`. In v5 this contract is stricter: it requires assumptions, risks, quality gates, and traceability in addition to functional and non-functional requirements.

A good specification answers:

- What exactly must the product do?
- Which behavior is out of scope?
- Which acceptance evidence proves each requirement?
- Which assumptions were chosen instead of blocking on routine ambiguity?
- Which risks and quality gates downstream stages must preserve?

## 2. Workflow Engineering

The `workflow_design` stage produces `.appforge/artifacts/workflow_spec.json`. v5 keeps workflow design a general contract, not only an automation-pipeline concern. The schema requires state model, compensation, concurrency, timeout policy, retries, idempotency, observability, manual override, and requirement traceability.

A good workflow contract answers:

- What starts the flow and what inputs are accepted?
- Which states and transitions exist?
- What happens on success, failure, timeout, cancellation, duplicate input, and retry?
- Which actions have external effects and how are they deduplicated?
- How can an operator inspect, replay, or stop work safely?

## 3. Memory Engineering

The new `memory_engineering` stage produces `.appforge/artifacts/memory_spec.json`. It defines user-visible, internal, durable, session, cache, audit, retry, job, and control-plane memory before code is written.

A good memory contract answers:

- What must the app remember across requests, refreshes, restarts, retries, and releases?
- Which component owns each state surface?
- Which writers and readers are allowed?
- How is stale, corrupt, partial, or migrated data handled?
- Which data must be retained, redacted, encrypted, or avoided?
- Which tests prove that memory survives the right lifecycle boundaries?

The runner also keeps its own compact stage memory ledger at `.appforge/memory/stage-memory.jsonl`. Later prompts include a redacted summary of previous outcomes, failures, decisions, and accepted engineering contracts.

## 4. Loop Engineering

The new `loop_engineering` stage produces `.appforge/artifacts/loop_spec.json`. It defines retry loops, polling loops, job/worker loops, reconciliation loops, UI refresh loops, validation loops, and human approval loops with explicit budgets and exits.

A good loop contract answers:

- Why does the loop exist?
- What enters and exits the loop?
- What progress signal proves convergence?
- What is the maximum iteration, time, or retry budget?
- What backoff, deduplication, idempotency, and cancellation rules apply?
- What happens when automation cannot prove safe progress?

The runner now detects repeated failure signatures and reports `REPEATED_FAILURE_LOOP` rather than burning through retries with the same failing repair path.

## Pipeline impact

All built-in pipelines keep their original purpose but now include or strengthen the four engineering stages before implementation-oriented stages. Examples:

```text
web-app:
intake → specification → workflow_design → memory_engineering → loop_engineering → architecture → experience → implementation → verification → security → release → handoff

prototype:
intake → specification → workflow_design → memory_engineering → loop_engineering → prototype_plan → experience → implementation → verification → demo → handoff

bugfix:
intake → reproduce → diagnosis → specification → workflow_design → memory_engineering → loop_engineering → fix → regression → security → release → handoff
```

## Files added or changed

- New skills: `stages/memory-engineering.md`, `stages/loop-engineering.md`, `meta/engineering-spine.md`
- New artifact schemas: `memory_spec.schema.json`, `loop_spec.schema.json`
- Strengthened schemas: `requirements_spec.schema.json`, `workflow_spec.schema.json`
- Runner memory ledger: `appforge/memory.py`, `.appforge/memory/stage-memory.jsonl`
- Runner retry-loop guard: repeated failure signatures become `REPEATED_FAILURE_LOOP`
- Pipeline manifests: all built-ins are versioned to `1.1` and include the v5 agentic spine
