# AppForge-LLM

![AppForge-LLM local AI app builder interface](intro.webp)

A local AI app builder that turns one prompt into a planned, tested, and previewable source project.

## Requirements

- Python 3.11+
- Node.js and npm
- Bun
- An LLM API key

## Windows 11

Double-click `build.bat`. It prepares the dependencies, starts the web app, and opens your default browser.

To launch it from Command Prompt:

```bat
build.bat
```

## macOS / Linux

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
