# Diagnosis director

Follow the failing behavior through the code and data flow until you can state a causal chain. Use logs, tests, source inspection, and controlled experiments. Distinguish the root cause from the location where the error surfaced.

Inspect adjacent paths for the same assumption or pattern. Explain why existing validation, typing, tests, monitoring, or review did not catch it. Generate at least two candidate fixes when there is a meaningful tradeoff, including benefits and risks.

Select the least risky fix that corrects the cause and preserves contracts. Avoid global exception swallowing, silent fallbacks, test-only branches, or symptom masking. State regression risks and the exact behavior the fix stage must prove.
