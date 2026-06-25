# Go stack skill

Keep packages cohesive and interfaces consumer-owned. Pass `context.Context` through I/O boundaries, wrap errors with useful context, and close resources deterministically. Prefer the standard library unless a dependency clearly helps. Use table-driven tests and `go test ./...`; run `go vet`. Make HTTP timeouts explicit and avoid unbounded goroutines.
