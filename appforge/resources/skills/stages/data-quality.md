# Data-quality director

Turn the data contract into executable checks. Validate schema, required fields, types, ranges, uniqueness, referential integrity, duplicates, time ordering, aggregation reconciliation, and known drift thresholds.

Test representative normal data and adversarial edge cases: empty inputs, all-null columns, unexpected categories, late events, duplicate batches, malformed timestamps, extreme values, and partial upstream failure. Ensure failures are surfaced with enough context to diagnose without exposing sensitive records.

Verify privacy behavior such as masking, minimization, retention, export, and deletion where applicable. Use synthetic fixtures. Do not copy production data into the repository.

Record datasets or fixtures, exact commands, evidence, failures, and limitations. A data pipeline that runs but silently produces invalid results does not pass.
