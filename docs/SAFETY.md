# Safety and trust model

OpenAppForge treats the coding agent as a powerful but fallible worker. Agent output is untrusted until independently validated.

## Default boundaries

- Workspace file paths are resolved against the adopted project root.
- AppForge network tools are disabled unless `allow_network=true` reaches the tool contract.
- Tools marked destructive require `allow_destructive=true`.
- Generated-project command execution uses argument vectors with `shell=False`, bounded timeouts, captured/redacted output, a scrubbed environment, a disposable home directory, and an operating-system sandbox. Execution fails closed when a supported sandbox is unavailable.
- Dependency installation is a capability of its own, `allow_dependency_install`, enabled by
  default and disabled with `--no-dependency-install` or `APPFORGE_ALLOW_DEPENDENCY_INSTALL=false`.
  It permits package managers to resolve dependencies whose writes land inside the workspace
  (`node_modules`, a workspace `.venv`, module caches). It does not open general network access,
  and `pip install` still requires `allow_destructive` unless it runs through a workspace `.venv`,
  because installing with the host interpreter would mutate the environment running AppForge.
- `run_command` executes non-destructive argument vectors without `allow_destructive`. Shells,
  inline interpreters (`-c`/`-e`), data-destroying executables (`rm`, `rmdir`, `shred`,
  `truncate`, `unlink`), and the blocked-pattern list all still require it.
- Commands run with `CI=true` and non-interactive package-manager defaults so watch-mode test
  runners terminate instead of hanging a gate until its timeout.
- Known destructive system, disk, force-push, and pipe-to-shell patterns are blocked by the command policy.
- Codex/Claude/generic local coding-agent CLI drivers were removed; each pipeline stage runs through the local LLM bridge against a configured external provider API key.
- The bridge binds to loopback only. Except for its minimal `/health` response, every route requires a separate high-entropy capability token. Managed startup keeps that capability in process memory and forwards only an allowlisted environment to the bridge.
- Provider API keys are never sent back to the browser. macOS uses Keychain by default; the file backend uses a `0700` directory, rejects symlinks and unsafe ownership/type, and atomically replaces `0600` files.
- `--unsafe-agent` is retained only for isolated environments and never bypasses artifact or gate validation.

The external LLM provider may produce incorrect or unsafe code. Its output is treated as untrusted text until AppForge's schema, gate, and review layers validate it. AppForge never lets the model execute commands or edit files directly; the bridge only returns a JSON envelope that the runner applies. Run untrusted application requests in a disposable workspace or container.

`--allow-network` governs declared network tools and sandbox egress. On macOS, generated commands remain unable to reach loopback services and receive remote egress only when network access is enabled. Linux uses Bubblewrap with a private network namespace and currently fails closed rather than offering remote egress; unsupported hosts also fail closed. A disposable container or VM remains useful defense in depth for highly untrusted input.


## Web application boundary

The v7 web server binds to `127.0.0.1` by default and has process-scoped single-user session protection, not account-based multi-user authentication. A one-time launch fragment is cleared before exchange for an `HttpOnly; SameSite=Strict` cookie. Protected routes also enforce loopback `Host` and exact scheme/host/port origin checks. Do not bind it to a public or shared network interface unless an authenticated reverse proxy, host firewall, and operating-system isolation are in place.

The web workflow leaves AppForge network-dependent tools disabled by default. Set `APPFORGE_ALLOW_NETWORK=true` only after reviewing the generated workspace and dependency sources. The trusted LLM bridge has independent provider-network access; generated-project processes do not receive its capability token or provider-key environment.

The web layer accepts only the natural-language request from the browser. Pipeline selection, project paths, driver configuration, safety flags, and archive paths are server-owned. Downloads are limited to the completed job's `.appforge/reports/` directory and remain unavailable until ZIP integrity verification succeeds.

## External actions

A pipeline may prepare deployment manifests, package metadata, migrations, or release commands. It does not authorize:

- deployment or production mutation;
- package publication;
- Git push, force-push, pull-request creation, or merge;
- paid infrastructure, purchases, or billing changes;
- outbound communication;
- credential creation or disclosure.

These actions require separate, explicit user approval outside the ordinary `forge` run.

## Secrets

Captured command output is redacted for common key/token/password patterns. Before release, the required secret-scan gate checks the source, and the archiver scans the exact inclusion set and the exact bytes it writes. It covers common provider keys, private keys, cloud credentials, bearer/JWT/npm tokens, and generic credential assignments without echoing matched secret text. Unreadable or oversized unscannable files fail closed.

The source archiver excludes:

- `.env` variants except `.env.example`;
- common private-key and key-store extensions;
- common credential filenames;
- `.git` and other VCS internals;
- dependency, build, coverage, and cache directories;
- the complete `.appforge/` control directory.

Secret scanning is heuristic and cannot prove absence. The archive is blocked on a finding or unscannable input, but a dedicated organizational secret scanner is still recommended before public release.

## Evidence integrity

The coding agent cannot complete a stage with prose alone. It must produce a fresh schema-valid completion record and all declared artifacts. Required quality tools are executed by the orchestrator after the agent returns. A missing command counts as skipped; a skipped required gate fails.

A gate whose runner is not installed reports `TOOLCHAIN_UNAVAILABLE` rather than a failure, because
the check never executed. When a required gate is blocked that way, the stage fails once as
`ENVIRONMENT_UNAVAILABLE` and is not retried: no repair to the generated code can install a missing
toolchain, so retrying only burns budget.

An attempt that exhausts its turn or token budget after submitting every required artifact and a
schema-valid stage result is accepted. The budget bounds how long an attempt may run; it does not
invalidate work the agent already finished and the orchestrator already validated.

No stage should label a test, target, migration, or user flow as verified unless the corresponding evidence was actually executed. Inferred support must be labeled as such.

## Recommended isolation

For unfamiliar requests or repositories:

1. use a disposable branch, worktree, container, or virtual machine;
2. keep `--unsafe-agent` off;
3. leave network access off until manifests and dependency sources are reviewed;
4. inspect `appforge prompt` and checkpoints in guided mode;
5. scan the final archive independently before sharing it.
