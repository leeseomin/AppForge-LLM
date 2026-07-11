# Safety and trust model

OpenAppForge treats the coding agent as a powerful but fallible worker. Agent output is untrusted until independently validated.

## Default boundaries

- Workspace file paths are resolved against the adopted project root.
- AppForge network tools are disabled unless `allow_network=true` reaches the tool contract.
- Tools marked destructive require `allow_destructive=true`.
- Command execution uses argument vectors with `shell=False`, bounded timeouts, and captured/redacted output.
- Known destructive system, disk, force-push, and pipe-to-shell patterns are blocked by the command policy.
- Codex/Claude/generic local coding-agent CLI drivers were removed; each pipeline stage runs through the local LLM bridge against a configured external provider API key.
- The bridge binds to the loopback interface only; provider API keys are persisted in a `0600`-permission config file and are never sent to the browser.
- `--unsafe-agent` is retained only for isolated environments and never bypasses artifact or gate validation.

The external LLM provider may produce incorrect or unsafe code. Its output is treated as untrusted text until AppForge's schema, gate, and review layers validate it. AppForge never lets the model execute commands or edit files directly; the bridge only returns a JSON envelope that the runner applies. Run untrusted application requests in a disposable workspace or container.

`--allow-network` governs AppForge's declared network tools; it is not an operating-system firewall. Repository test and build commands execute project code and can create subprocesses or network connections permitted by the host. Use a container, VM, or host firewall when the repository or generated code is untrusted.


## Web application boundary

The v5 web server binds to `127.0.0.1` by default and has no built-in multi-user authentication. Do not bind it to a public or shared network interface unless an authenticated reverse proxy, host firewall, and operating-system isolation are in place.

The web workflow enables AppForge network-dependent tools by default to make dependency installation and release checks achievable through the single-click path. Set `APPFORGE_ALLOW_NETWORK=false` to restore an offline tool policy. This setting is still not an operating-system firewall and does not constrain arbitrary network activity performed by repository build scripts or the LLM bridge process.

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

Captured command output is redacted for common key/token/password patterns. The release process scans text source for likely private keys, cloud access keys, GitHub-style tokens, and generic credential assignments.

The source archiver excludes:

- `.env` variants except `.env.example`;
- common private-key and key-store extensions;
- common credential filenames;
- `.git` and other VCS internals;
- dependency, build, coverage, and cache directories;
- the complete `.appforge/` control directory.

Secret scanning is heuristic and cannot prove absence. Use a dedicated organizational secret scanner before public release when available.

## Evidence integrity

The coding agent cannot complete a stage with prose alone. It must produce a fresh schema-valid completion record and all declared artifacts. Required quality tools are executed by the orchestrator after the agent returns. A missing command counts as skipped; a skipped required gate fails.

No stage should label a test, target, migration, or user flow as verified unless the corresponding evidence was actually executed. Inferred support must be labeled as such.

## Recommended isolation

For unfamiliar requests or repositories:

1. use a disposable branch, worktree, container, or virtual machine;
2. keep `--unsafe-agent` off;
3. leave network access off until manifests and dependency sources are reviewed;
4. inspect `appforge prompt` and checkpoints in guided mode;
5. scan the final archive independently before sharing it.
