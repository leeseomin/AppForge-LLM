from __future__ import annotations

import os
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from .constants import MAX_CAPTURE_CHARS
from .models import DriverResult, ProjectLayout
from .util import atomic_write_text, command_exists, redact, truncate


class DriverError(RuntimeError):
    pass


class AgentDriver(ABC):
    name: str

    @abstractmethod
    def run(self, prompt: str, *, layout: ProjectLayout, stage: str, attempt: int, timeout: int) -> DriverResult:
        raise NotImplementedError


def _execute(
    argv: list[str],
    *,
    cwd: Path,
    stdin: str | None,
    timeout: int,
    final_message_path: Path | None = None,
) -> DriverResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
            env=os.environ.copy(),
            check=False,
        )
        stdout = redact(truncate(completed.stdout, MAX_CAPTURE_CHARS))
        stderr = redact(truncate(completed.stderr, MAX_CAPTURE_CHARS))
        if final_message_path is not None and not final_message_path.exists():
            atomic_write_text(final_message_path, stdout)
        return DriverResult(
            success=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(time.monotonic() - started, 4),
            command=argv,
            final_message_path=str(final_message_path) if final_message_path else None,
        )
    except subprocess.TimeoutExpired as exc:
        return DriverResult(
            success=False,
            exit_code=124,
            stdout=redact(truncate(exc.stdout or "", MAX_CAPTURE_CHARS)),
            stderr=redact(truncate(exc.stderr or "", MAX_CAPTURE_CHARS)),
            duration_seconds=round(time.monotonic() - started, 4),
            command=argv,
            final_message_path=str(final_message_path) if final_message_path else None,
        )


class CodexDriver(AgentDriver):
    name = "codex"

    def __init__(self, *, unsafe: bool = False, model: str | None = None) -> None:
        self.unsafe = unsafe
        self.model = model

    def run(self, prompt: str, *, layout: ProjectLayout, stage: str, attempt: int, timeout: int) -> DriverResult:
        if not command_exists("codex"):
            raise DriverError("Codex CLI is not installed or not on PATH")
        final_path = layout.logs / f"{stage}-attempt-{attempt}-codex-final.txt"
        argv = ["codex", "exec", "--cd", str(layout.root), "--skip-git-repo-check", "--output-last-message", str(final_path)]
        if self.model:
            argv += ["--model", self.model]
        if self.unsafe:
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            argv += ["--sandbox", "workspace-write"]
        argv.append("-")
        return _execute(argv, cwd=layout.root, stdin=prompt, timeout=timeout, final_message_path=final_path)


class ClaudeDriver(AgentDriver):
    name = "claude"

    def __init__(self, *, unsafe: bool = False, model: str | None = None, max_turns: int | None = None) -> None:
        self.unsafe = unsafe
        self.model = model
        self.max_turns = max_turns

    def run(self, prompt: str, *, layout: ProjectLayout, stage: str, attempt: int, timeout: int) -> DriverResult:
        if not command_exists("claude"):
            raise DriverError("Claude Code CLI is not installed or not on PATH")
        final_path = layout.logs / f"{stage}-attempt-{attempt}-claude-final.txt"
        argv = ["claude", "-p", "--output-format", "text"]
        if self.unsafe:
            argv.append("--dangerously-skip-permissions")
        else:
            argv += ["--permission-mode", "auto"]
        if self.model:
            argv += ["--model", self.model]
        if self.max_turns:
            argv += ["--max-turns", str(self.max_turns)]
        # Print mode accepts piped input; stdin avoids OS argument-length limits and
        # keeps the full stage packet out of process listings.
        result = _execute(argv, cwd=layout.root, stdin=prompt, timeout=timeout, final_message_path=final_path)
        atomic_write_text(final_path, result.stdout)
        return result


class GenericCommandDriver(AgentDriver):
    name = "generic"

    def __init__(self, command_template: str) -> None:
        if not command_template.strip():
            raise DriverError("A generic driver requires --agent-cmd")
        self.command_template = command_template

    def run(self, prompt: str, *, layout: ProjectLayout, stage: str, attempt: int, timeout: int) -> DriverResult:
        prompt_path = layout.prompts / f"{stage}-attempt-{attempt}.md"
        final_path = layout.logs / f"{stage}-attempt-{attempt}-generic-final.txt"
        atomic_write_text(prompt_path, prompt)
        tokens = shlex.split(self.command_template, posix=os.name != "nt")
        context = {
            "workspace": str(layout.root),
            "prompt_file": str(prompt_path),
            "result_file": str(final_path),
            "stage": stage,
            "attempt": str(attempt),
        }
        argv = [token.format(**context) for token in tokens]
        uses_prompt_file = any("{prompt_file}" in token for token in tokens)
        result = _execute(argv, cwd=layout.root, stdin=None if uses_prompt_file else prompt, timeout=timeout, final_message_path=final_path)
        if not final_path.exists():
            atomic_write_text(final_path, result.stdout)
        return result


class LLMBridgeDriver(AgentDriver):
    """Drive a stage with an external LLM through the local Node/Bun bridge.

    The bridge (``llm_bridge/``) reuses the coco ``@opencode-ai/llm`` engine to
    call any configured provider/model. Unlike the Codex/Claude CLIs this driver
    is single-shot per stage: it returns the model's completion as the stage's
    final message. File-writing agentic stages should still use a CLI driver.
    """

    name = "llm-bridge"

    def __init__(
        self,
        *,
        bridge_url: str,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        if not bridge_url:
            raise DriverError("LLM bridge driver requires APPFORGE_LLM_BRIDGE_URL")
        self.bridge_url = bridge_url
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens

    def run(self, prompt: str, *, layout: ProjectLayout, stage: str, attempt: int, timeout: int) -> DriverResult:
        from . import llm_bridge

        final_path = layout.logs / f"{stage}-attempt-{attempt}-llm-bridge-final.txt"
        started = time.monotonic()
        duration = lambda: round(time.monotonic() - started, 4)
        try:
            result = llm_bridge.generate(
                self.bridge_url,
                prompt=prompt,
                system=(
                    "You are a precise software-production agent. Follow the stage "
                    "instructions exactly and return the requested artifact."
                ),
                provider=self.provider,
                model=self.model,
                max_tokens=self.max_tokens,
                timeout=max(timeout, 60),
            )
        except llm_bridge.BridgeError as exc:
            return DriverResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration(),
                command=["llm-bridge", "/generate"],
                final_message_path=str(final_path),
            )
        text = str(result.get("text") or "")
        atomic_write_text(final_path, text)
        return DriverResult(
            success=True,
            exit_code=0,
            stdout=text,
            stderr="",
            duration_seconds=duration(),
            command=["llm-bridge", "/generate"],
            final_message_path=str(final_path),
        )


def create_driver(
    name: str,
    *,
    unsafe: bool = False,
    model: str | None = None,
    agent_cmd: str | None = None,
    max_turns: int | None = None,
    bridge_url: str | None = None,
    llm_provider: str | None = None,
) -> AgentDriver:
    normalized = name.casefold()
    if normalized == "auto":
        if command_exists("codex"):
            normalized = "codex"
        elif command_exists("claude"):
            normalized = "claude"
        else:
            raise DriverError(
                "No supported coding-agent CLI found. Install Codex or Claude Code, "
                "use --driver generic --agent-cmd ..., or run the agent-native loop from AGENT_GUIDE.md."
            )
    if normalized == "codex":
        return CodexDriver(unsafe=unsafe, model=model)
    if normalized == "claude":
        return ClaudeDriver(unsafe=unsafe, model=model, max_turns=max_turns)
    if normalized == "generic":
        return GenericCommandDriver(agent_cmd or "")
    if normalized in {"llm-bridge", "llm_bridge", "llm"}:
        return LLMBridgeDriver(bridge_url=bridge_url or "", provider=llm_provider, model=model)
    raise DriverError(f"Unknown driver {name!r}; use auto, codex, claude, generic, or llm-bridge")
