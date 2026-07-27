# Testing strategy skill

Build a pyramid around behavior: many deterministic unit tests, focused integration tests across real boundaries, and a few end-to-end smoke tests. Give every regression a failing-before test. Avoid asserting private implementation details, arbitrary sleeps, global mutable fixtures, and live third-party services. Test negative paths and authorization. A green suite is meaningful only when the command actually ran and the assertions cover the promised behavior.
