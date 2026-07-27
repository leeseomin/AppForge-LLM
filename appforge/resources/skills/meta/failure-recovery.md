# Failure recovery

Treat failure as evidence, not as permission to lower the bar.

1. Reproduce the exact failed command with the same working directory and configuration.
2. Classify it: code defect, test defect, missing dependency, environment mismatch, permission/authentication, flaky external service, or incorrect product assumption.
3. Fix the narrowest root cause. Do not delete tests, broaden exceptions, disable type checks, or replace real behavior with mocks merely to obtain green output.
4. Add or improve a test when the failure reveals an uncovered behavior.
5. Rerun the failed check and the nearest broader suite.
6. Record any environment limitation in the artifact and stage result.

After repeated failure, stop with a precise blocker instead of looping or pretending success.
