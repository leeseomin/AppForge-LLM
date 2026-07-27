# Release director

Create a reproducible release candidate from the verified source. Run the production build from a clean-enough state and identify its outputs. Ensure lockfiles, generated code, migrations, assets, and configuration examples are present.

Update README with installation, configuration, start, test, and build commands. Add a changelog or release notes when appropriate. Document version, migrations, known issues, rollback, and platform requirements.

Run release readiness, build, and secret checks. Verify that archives and images exclude `.env`, credentials, caches, local databases, and development-only artifacts. Do not sign, publish, push, deploy, or upload without explicit authorization.

The release report must contain evidence rather than promises. If the environment cannot produce a target artifact, document the exact missing prerequisite and provide the command that a compatible environment should run.
