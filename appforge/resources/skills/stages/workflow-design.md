# Workflow-design director

Describe the automation as a state machine. Define trigger, inputs, ordered steps, outputs, credentials, timeouts, retries, idempotency, and terminal states. Each step must state success behavior and failure behavior.

Separate read-only discovery from consequential actions. Make external effects deduplicated and traceable. Use stable idempotency keys, checkpoints, or processed-event records so retries do not send duplicates or corrupt state.

Define rate-limit handling, exponential backoff, dead-letter or quarantine behavior, and a manual override. State how operators will see progress, inspect errors, replay safe work, and stop the automation.

Credentials must be least-privilege and externally configured. Never place secrets in workflow definitions or logs. If the automation can delete, publish, charge, message, or modify production data, require an explicit approval boundary.
