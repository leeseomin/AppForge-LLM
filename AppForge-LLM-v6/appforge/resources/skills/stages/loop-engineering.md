# Loop-engineering director

Design every feedback loop so it converges, stops, or escalates safely. A loop can be a retry policy, polling worker, scheduler, reconciliation job, UI refresh, queue consumer, agent repair cycle, validation pass, or human approval loop.

Start from the workflow and memory contracts. For each loop, define purpose, entry conditions, state read/write behavior, progress signal, maximum iterations or time budget, backoff, deduplication, idempotency key, exit condition, and failure policy. A loop without an exit condition is a blocker.

Separate loops that are safe to repeat from loops with external effects. Consequential actions such as charging, publishing, messaging, deleting, or modifying production data require deduplication and a human escape hatch. Polling and background loops must avoid hot spins, thundering herds, and hidden infinite retries.

Design observability for loops: counters, last-success timestamps, dead-letter/quarantine records, user-visible state, and actionable logs. Include cancellation and manual recovery when automation cannot prove safe progress.

The `loop_spec` artifact must make implementation choices boring: engineers should know how to wire retries, workers, UI polling, validation loops, and recovery without inventing new behavior downstream.
