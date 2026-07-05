# Data-model director

Derive persistent entities from requirements and user flows. For each entity, define purpose, fields, types, requiredness, validation, ownership, and sensitive-data classification. Make identifiers, timestamps, lifecycle state, and deletion behavior explicit.

Model relationships and integrity rules before choosing convenience shortcuts. Decide which constraints belong in the database, domain layer, or user interface. Address uniqueness, concurrency, idempotency keys, soft versus hard deletion, and audit history only where the product needs them.

Plan migrations that can run safely on existing data. Prefer additive changes and backfills before destructive changes. Include seed or sample data that is deterministic, non-sensitive, and covers edge cases.

For SaaS or multi-user products, define tenant boundaries and ensure every access path can enforce them. For personal or regulated data, state retention, export, deletion, and logging constraints. Never use real credentials or personal data in fixtures.

The resulting data contract must be implementable and testable, not an abstract diagram detached from code.
