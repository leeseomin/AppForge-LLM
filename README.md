# AppForge-LLM

![AppForge-LLM local AI app builder interface](intro.webp)

A local AI app builder that turns one prompt into a planned, tested, and previewable source project.

## Requirements

- Python 3.11+
- Node.js and npm
- Bun
- An LLM API key

## Windows 11

Use a local **NTFS** folder and install Python 3.11+, Node.js/npm, and the Bun version pinned in `.bun-version`.

Run the security and dependency preflight first:

```bat
build.bat --check
```

Then double-click `build.bat`, or launch it from Command Prompt:

```bat
build.bat
```

Generated project commands run in a per-workspace **AppContainer** with a kill-on-close **Job Object**. Network access is absent by default and is added only for an explicitly approved invocation. Windows API keys default to **DPAPI CurrentUser** encryption; `providers.json` keeps references rather than plaintext secrets.

See [Windows 11 support and troubleshooting](docs/WINDOWS_11.md) for the isolation model, resource limits, CI gates, and recovery steps.

## macOS 

Run this command from the project directory:

```bash
./build.sh
```

The launcher prepares the dependencies, starts the web app, and opens your default browser.

The first launch may download dependencies. When the browser opens, connect an LLM and describe the app you want to build.

## Core pipeline

1. Accept one natural-language app request with its runtime and safety settings.
2. Run preflight checks and select the best versioned pipeline for the request.
3. Create an isolated project workspace with durable job state and checkpoints.
4. Turn the request into validated requirements, workflows, and architecture artifacts.
5. Send bounded stage prompts through the local LLM bridge to the selected provider.
6. Let the bridge agent implement source code, tests, and documentation with constrained tools.
7. Validate every stage with schemas, reviews, tests, lint, type checks, builds, and security gates.
8. Retry failures with structured evidence and bounded repair attempts, or stop with a clear error.
9. Build a preview, preserve the evidence, and package the verified source for download.

## License

Apache-2.0
