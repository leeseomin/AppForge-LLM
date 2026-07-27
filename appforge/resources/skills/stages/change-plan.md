# Change-plan director

Create a minimal plan for the existing repository. Map each proposed change to a requirement and to specific files or architectural areas. Explain why each change belongs there and how it will be verified.

Preserve public interfaces, migrations, compatibility, configuration, and current behavior unless the request explicitly changes them. Identify new tests before implementation. Include any data migration, feature flag, deprecation, rollout, and rollback needs.

Reject broad cleanup that is not necessary for the requested behavior. If a small refactor is required to make the change safe, bound it and explain the dependency. Distinguish confirmed file locations from areas that still require implementation-time discovery.

The definition of done must be executable: commands, acceptance behavior, and evidence. A good plan reduces implementation uncertainty without rewriting the architecture stage.
