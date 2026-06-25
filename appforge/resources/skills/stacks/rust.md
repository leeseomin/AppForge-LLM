# Rust stack skill

Model invalid states out of the type system where practical. Use `Result` with contextual errors rather than panics in normal paths. Keep unsafe code absent or tightly justified. Add unit tests near modules and integration tests for public behavior. Run `cargo fmt`, `cargo clippy -- -D warnings`, tests, and a release build.
