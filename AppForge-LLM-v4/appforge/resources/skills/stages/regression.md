# Regression director

Run the original failing case first and show that it now passes for the correct reason. Then run the nearest unit, integration, and full suites supported by the repository. Include build, lint, and type checks where available.

Use the diagnosis risk list to test adjacent variants, not random unrelated behavior. For stateful defects, test repeated execution and rollback. For concurrency defects, make the interleaving deterministic. For parsing or validation defects, add boundary and malformed inputs.

Review the final diff to ensure the fix did not broaden unexpectedly. The regression report must include exact command evidence, remaining failures, and a confidence statement proportional to the tests actually run.
