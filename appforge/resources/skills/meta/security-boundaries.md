# Security boundaries

Assume all external input is hostile until validated. Separate authentication (who) from authorization (what they may do). Enforce permissions server-side, not only in the UI. Use parameterized queries and safe framework APIs. Store secrets in environment or a secret manager and provide placeholders only in `.env.example`. Avoid logging credentials, session tokens, personal data, or full request bodies by default.

Do not weaken TLS, CORS, cookie flags, certificate validation, sandboxing, dependency audits, or static analysis to make development easier. For file operations, normalize paths and prevent traversal. For uploads, limit size/type and store outside executable paths. For webhooks, verify signatures and make handling idempotent. For automation, scope credentials and provide a manual kill switch.
