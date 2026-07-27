# API-design director

Design from client tasks and domain operations, not database tables. Define conventions first: transport, versioning, naming, authentication, authorization, pagination, filtering, idempotency, correlation IDs, and error envelope.

For every endpoint or operation, specify method, path or name, purpose, permissions, request contract, successful responses, validation failures, domain failures, and examples. Use stable error codes and explain the client action. Avoid leaking internal stack traces or sensitive details.

Make mutating operations safe under retries. Define concurrency behavior and conditional updates where stale writes matter. For collections, bound page sizes. For webhooks, define signature verification, replay protection, retry policy, and acknowledgement timing.

Prefer backward-compatible evolution. Record what constitutes a breaking change and how deprecation will work. Keep the initial API surface narrow; do not expose internal abstractions without a real client need.
