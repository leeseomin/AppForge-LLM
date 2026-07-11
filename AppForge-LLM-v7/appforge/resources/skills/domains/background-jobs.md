# Background jobs

Make jobs idempotent, bounded, observable, and retry-safe. Persist status and attempt information. Separate transient from permanent failure, use exponential backoff with limits, and provide dead-letter or manual replay. Do not acknowledge work before durable handoff. Test duplicate delivery, worker crash, timeout, and partial external effects.
