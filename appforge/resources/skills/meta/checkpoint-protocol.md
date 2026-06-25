### Checkpoint and artifact protocol

The `.appforge/` directory is the durable control plane for this project.

- Write each required stage artifact to `.appforge/artifacts/<artifact-name>.json`.
- Write `.appforge/stage-result.json` last, after all work and self-review.
- Keep artifact statements factual and point to concrete files or command evidence.
- Do not edit checkpoint files directly; the AppForge runner owns `.appforge/checkpoints/`.
- Prompts and captured agent output live under `.appforge/prompts/` and `.appforge/logs/`.

For long work, keep source files in a runnable state after each coherent slice. A failed attempt may be resumed from the repository and prior artifacts, so do not depend on unstored conversational context.

When the stage is genuinely blocked, write the artifact as far as its schema allows and set the stage result to `blocked`. State exactly what was attempted, the error, the missing prerequisite, and the safest next action. Never mark a blocked or partially implemented stage as complete.
