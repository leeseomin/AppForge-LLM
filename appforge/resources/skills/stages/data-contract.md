# Data-contract director

Define the data that enters, moves through, and leaves the application. Document owners, formats, schemas, units, time zones, null semantics, keys, update cadence, lineage, and allowed ranges.

List transformations in order and state whether they are lossy, reversible, deterministic, or stateful. Define behavior for missing, duplicated, late, malformed, or out-of-order records. Make schema evolution and backward compatibility explicit.

Specify validation checks that can become code: uniqueness, completeness, referential integrity, domain ranges, drift thresholds, and reconciliation totals. Include representative sample data with boundary cases, but never real personal or secret information.

Identify sensitive columns and privacy constraints. State where masking, aggregation, deletion, or access restrictions apply. For analytics, distinguish event time from processing time and define metric formulas precisely enough to prevent competing interpretations.

The contract should allow implementation and data-quality stages to operate without guessing what “valid data” means.
