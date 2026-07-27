### Stage self-review protocol

Before declaring the stage complete, perform a separate review pass.

**Evidence first.** Every finding must point to a file, artifact field, command result, test, or observable behavior. When evidence is unavailable, mark the item as an investigation rather than pretending certainty.

**Review in this order:**

1. Validate every required artifact against the supplied schema.
2. Compare the work with each success criterion and review-focus item in the stage packet.
3. Trace changed behavior back to requirement IDs or the diagnosed defect.
4. Inspect unhappy paths: empty input, invalid input, permission denial, missing configuration, network failure, retry, cancellation, concurrency, and data migration where relevant.
5. Check maintainability: clear names, cohesive modules, no needless duplication, comments for non-obvious decisions, and no hidden global state.
6. Check security and privacy: trust boundaries, secret handling, authorization, injection, logging, sensitive data, dependencies, and safe defaults.
7. Run the relevant test, lint, type-check, build, and smoke commands. Capture exact commands and results.

**Severity:**

- `critical`: blocks the stage. Include an exact corrective action.
- `suggestion`: worthwhile quality improvement that does not invalidate the artifact. Include a proposed change.
- `investigation`: concrete concern that needs more evidence.
- `nitpick`: cosmetic polish only.

A failed required command, invalid artifact, placeholder implementation, missing acceptance behavior, committed credential, or fabricated evidence is always critical. Fix critical findings and repeat the review before completion.
