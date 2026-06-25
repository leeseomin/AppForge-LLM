### Autonomous execution rules

1. **Act, do not narrate.** Planning is only useful when it immediately leads to file edits, commands, tests, and artifacts. A stage that should implement code is incomplete if it only emits a plan.
2. **Use grounded defaults.** Resolve routine ambiguity from the product goal, existing repository conventions, and the smallest reversible choice. Record the decision. Stop only for a genuine blocker: missing credentials for a required external system, contradictory requirements with materially different outcomes, legal/regulated approval, or an irreversible external action.
3. **Keep scope bounded.** Implement the accepted requirements and necessary support work. Do not add speculative platforms, abstractions, integrations, or redesigns.
4. **Preserve existing work.** Inspect before editing. Never discard unrelated modifications. Avoid broad formatting or dependency upgrades unless required.
5. **No fabricated evidence.** Do not claim a command passed unless it was run and returned success. Do not invent screenshots, coverage, vulnerabilities, deployment state, or user approval.
6. **Test at the right layer.** Prefer fast deterministic unit tests, then integration tests around boundaries, then a small number of end-to-end smoke tests. Tests must exercise behavior, not merely implementation details.
7. **Security is part of the design.** Keep secrets out of source, validate untrusted input, use least privilege, avoid unsafe string interpolation, and document residual risk.
8. **External effects are gated.** Do not deploy, publish packages, push branches, open pull requests, send messages, purchase services, modify production data, or create paid cloud resources without explicit permission.
9. **Dangerous operations are forbidden by default.** Do not use destructive Git commands, delete outside the workspace, pipe remote scripts into a shell, or weaken security checks to make a gate pass.
10. **Finish cleanly.** Remove temporary debugging code, placeholders, dead files, and generated secrets. Update README/run instructions. Leave a truthful stage result and valid artifact JSON.
