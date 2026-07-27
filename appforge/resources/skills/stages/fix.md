# Fix director

Implement the selected root-cause correction with the smallest safe change surface. Preserve unrelated behavior and repository style. The reproduction test should fail before the fix and pass after it; keep that test as permanent protection unless there is a documented reason not to.

Do not bundle opportunistic refactors, dependency upgrades, or formatting churn. When a supporting refactor is unavoidable, separate it conceptually and prove behavior remains equivalent.

Check adjacent call sites and data variants identified during diagnosis. Improve validation, types, or error reporting where that directly prevents recurrence. Avoid catches that hide the error, retries that amplify side effects, and defaults that corrupt data.

Record exact files, commands, and why the fix addresses the cause. The regression stage will independently verify the result.
