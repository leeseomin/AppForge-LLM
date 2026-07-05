### V5 agentic engineering spine

Every production stage must preserve the same hardened chain:

1. **Specification Engineering** — convert intent into stable, testable contracts. Use requirement IDs, measurable acceptance criteria, explicit assumptions, non-goals, risks, and traceability. Do not begin structural or code work from vague intent.
2. **Workflow Engineering** — convert the specification into restartable user/system flows. Define states, triggers, inputs, outputs, success paths, failure paths, retries, idempotency, cancellation, observability, and manual recovery.
3. **Memory Engineering** — define what the app, automation, agent, or operator must remember across requests, sessions, retries, releases, and incidents. Include retention, privacy, serialization format, reconciliation, migrations, cache invalidation, and auditability.
4. **Loop Engineering** — define feedback loops, retry loops, polling loops, job loops, reconciliation loops, and human review loops with exit conditions. Every loop needs a budget, progress signal, convergence rule, backoff, dead-letter/escape path, and evidence that it cannot spin forever.

Downstream work must cite upstream contracts. Implementation should not invent behavior that is absent from the specification/workflow/memory/loop artifacts unless it records a reversible decision and updates the relevant artifact.
