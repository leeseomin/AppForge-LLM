from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .artifacts import load_artifact_schema
from .constants import IGNORED_DIRS, SKILLS_DIR, STAGE_RESULT_FILE_NAME
from .memory import render_memory_context
from .models import PipelineSpec, ProjectLayout, StageSpec
from .tooling.detection import detect_stack
from .tooling.registry import ToolRegistry
from .util import read_json, safe_resolve, truncate


def load_skill(relative_path: str) -> str:
    path = safe_resolve(SKILLS_DIR, relative_path)
    if not path.exists():
        raise FileNotFoundError(f"Skill not found: {relative_path}")
    return path.read_text(encoding="utf-8")


def _workspace_tree(layout: ProjectLayout, max_entries: int = 400) -> str:
    result = ToolRegistry().get("workspace_tree").run(layout.root, {"max_depth": 5, "max_entries": max_entries})
    if not result.success:
        return f"<workspace tree unavailable: {result.error}>"
    entries = result.data.get("entries") or []
    return "\n".join(f"- {entry}" for entry in entries) or "- <empty workspace>"


_ARTIFACT_CONTEXT_BUDGETS = {
    "architecture_spec": 30_000,
    "requirements_spec": 24_000,
    "workflow_spec": 20_000,
    "memory_spec": 20_000,
    "loop_spec": 20_000,
    "experience_spec": 18_000,
}


def _prior_artifacts(layout: ProjectLayout, pipeline: PipelineSpec, current_stage: str) -> str:
    chunks: list[str] = []
    for stage in pipeline.stages:
        if stage.name == current_stage:
            break
        for artifact in stage.produces:
            path = layout.artifacts / f"{artifact}.json"
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            limit = _ARTIFACT_CONTEXT_BUDGETS.get(artifact, 12_000)
            chunks.append(f"### {artifact}\n```json\n{truncate(text, limit)}\n```")
    return "\n\n".join(chunks) or "<none>"


def _artifact_contracts(stage: StageSpec) -> str:
    chunks: list[str] = []
    for artifact in stage.produces:
        schema = load_artifact_schema(artifact)
        chunks.append(
            f"### `{artifact}`\nWrite: `.appforge/artifacts/{artifact}.json`\n"
            f"Schema:\n```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```"
        )
    return "\n\n".join(chunks)


def _relevant_knowledge(layout: ProjectLayout, prompt: str) -> str:
    detected = detect_stack(layout.root)
    names: list[tuple[str, str]] = []
    stack_map = {
        "python": "stacks/python.md",
        "javascript/typescript": "stacks/typescript.md",
        "go": "stacks/go.md",
        "rust": "stacks/rust.md",
        "dart": "stacks/flutter.md",
    }
    framework_map = {
        "react": "stacks/react.md",
        "nextjs": "stacks/nextjs.md",
        "express": "stacks/node.md",
        "nestjs": "stacks/node.md",
        "fastapi": "stacks/fastapi.md",
        "django": "stacks/django.md",
        "flutter": "stacks/flutter.md",
        "electron": "stacks/electron-tauri.md",
        "tauri": "stacks/electron-tauri.md",
        "docker": "stacks/docker.md",
    }
    for value in detected.get("languages", []):
        if value in stack_map:
            names.append((value, stack_map[value]))
    for value in detected.get("frameworks", []):
        if value in framework_map:
            names.append((value, framework_map[value]))
    if detected.get("manifests"):
        names.append(("testing", "stacks/testing.md"))

    lowered = prompt.casefold()
    domain_map = {
        "auth": ("auth", "login", "sign in", "로그인", "인증", "권한"),
        "payments": ("payment", "billing", "subscription", "결제", "구독"),
        "multi-tenancy": ("tenant", "multi-tenant", "멀티테넌트"),
        "file-uploads": ("upload", "file", "첨부", "업로드"),
        "background-jobs": ("queue", "background job", "worker", "작업 큐", "백그라운드"),
        "realtime": ("realtime", "websocket", "실시간", "채팅"),
        "observability": ("observability", "monitoring", "logging", "모니터링", "로그"),
        "i18n": ("i18n", "localization", "다국어", "번역"),
        "accessibility": ("accessibility", "a11y", "접근성"),
        "privacy": ("privacy", "personal data", "개인정보", "프라이버시"),
    }
    for name, needles in domain_map.items():
        if any(needle in lowered for needle in needles):
            names.append((name, f"domains/{name}.md"))

    seen: set[str] = set()
    chunks: list[str] = []
    for label, path in names:
        if path in seen:
            continue
        seen.add(path)
        chunks.append(f"### {label}\n{load_skill(path)}")
    return "\n\n".join(chunks) or "<No additional stack/domain skill selected yet. Inspect the repository and apply its conventions.>"


_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.@+/-]+\.(?:py|js|jsx|ts|tsx|vue|json|yaml|yml|toml|css|html|md|txt|sh|go|rs|java|kt|swift|dart|sql))(?![A-Za-z0-9_.-])"
)

_ENTRYPOINT_HINTS = (
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "vite.config.ts",
    "vite.config.js",
    "src/main.ts",
    "src/main.tsx",
    "src/App.vue",
    "src/App.tsx",
    "src/App.jsx",
    "src/index.tsx",
    "src/index.jsx",
    "app/main.py",
    "main.py",
    "pyproject.toml",
    "requirements.txt",
    "README.md",
)


def _is_managed_or_ignored_path(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    return bool(parts) and (parts[0] in {".appforge", ".git", ".hg", ".svn"} or any(part in IGNORED_DIRS for part in parts))


def _candidate_paths_from_payload(payload: Any) -> list[str]:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except TypeError:
        text = str(payload)
    candidates: list[str] = []
    for match in _PATH_PATTERN.finditer(text):
        value = match.group(1).strip("'\"`.,:;()[]{}<>")
        if value and not value.startswith(("http://", "https://")):
            candidates.append(value)
    return candidates


def _read_relevant_file(layout: ProjectLayout, relative_path: str, *, max_chars: int) -> str | None:
    if _is_managed_or_ignored_path(relative_path):
        return None
    try:
        path = safe_resolve(layout.root, relative_path)
    except ValueError:
        return None
    try:
        rel = path.relative_to(layout.root).as_posix()
    except ValueError:
        return None
    if _is_managed_or_ignored_path(rel) or not path.is_file() or path.is_symlink():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192] or len(raw) > 1_000_000:
        return None
    text = raw.decode("utf-8", errors="replace")
    return f"### {rel}\n```\n{truncate(text, max_chars)}\n```"


def _relevant_file_contents(
    layout: ProjectLayout,
    stage: StageSpec,
    *,
    previous_failure: dict[str, Any] | None = None,
    budget_chars: int = 60_000,
) -> str:
    """Return full-ish contents for files the current stage can plausibly edit.

    v4 only exposed a workspace tree; v5 adds bounded file bodies so first attempts
    and repairs can make targeted diffs instead of regenerating whole projects.
    """
    if stage.name not in {"implementation", "verification", "fix", "regression", "security", "release", "handoff"} and not previous_failure:
        return "<not needed for this planning-oriented stage>"

    candidates: list[str] = []
    if previous_failure:
        candidates.extend(_candidate_paths_from_payload(previous_failure))
        for changed in (previous_failure.get("files_changed") or []):
            if isinstance(changed, str):
                candidates.append(changed)
    candidates.extend(_ENTRYPOINT_HINTS)

    seen: set[str] = set()
    chunks: list[str] = []
    remaining = budget_chars
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        chunk = _read_relevant_file(layout, candidate, max_chars=min(20_000, max(2_000, remaining)))
        if not chunk:
            continue
        chunks.append(chunk)
        remaining -= len(chunk)
        if remaining <= 1_000:
            break
    return "\n\n".join(chunks) or "<no directly relevant readable files found within budget>"


def build_repair_stage_prompt(
    layout: ProjectLayout,
    *,
    project: dict[str, Any],
    pipeline: PipelineSpec,
    stage: StageSpec,
    attempt: int,
    previous_failure: dict[str, Any],
) -> str:
    stage_skill = load_skill(stage.skill)
    reviewer = load_skill("meta/reviewer.md")
    autonomy = load_skill("meta/autonomous-execution.md")
    stage_result_schema = read_json(Path(__file__).resolve().parent / "resources" / "schemas" / "stage-result.schema.json", {})
    tool_info = {name: ToolRegistry().get(name).info() for name in stage.tools if name in ToolRegistry().names()}
    repair_kind = previous_failure.get("next_retry_mode") or previous_failure.get("repair_mode") or "repair"
    strategy_instruction = (
        "This is a targeted repair attempt. Fix only the failed evidence below and preserve unrelated files."
        if repair_kind != "regenerate"
        else "The previous repair path repeated the same failure. Regenerate the affected implementation strategy, not merely the same patch."
    )
    return f"""# OpenAppForge v6 targeted repair packet

You are repairing one failed stage of a governed software-production pipeline.
{strategy_instruction}

## Project
- Name: `{project['name']}`
- Original request: {project['prompt']}
- Pipeline: `{pipeline.name}` — {pipeline.description}
- Mode: `{project['mode']}`
- Stage: `{stage.name}` (attempt {attempt})
- Stage purpose: {stage.description}

## Non-negotiable operating contract
{autonomy}

## Stage director skill
{stage_skill}

## Review protocol
{reviewer}

## Failed gate / repair evidence
Use this as evidence, not as prose to ignore. Do not report completion until it is fixed or truthfully blocked.
```json
{json.dumps(previous_failure, ensure_ascii=False, indent=2)}
```

## Files implicated or likely relevant
{_relevant_file_contents(layout, stage, previous_failure=previous_failure, budget_chars=70_000)}

## Available AppForge tools
```json
{json.dumps(tool_info, ensure_ascii=False, indent=2)}
```

## Existing workspace tree
{_workspace_tree(layout)}

## Prior accepted artifacts
{_prior_artifacts(layout, pipeline, stage.name)}

## Required stage artifacts
{_artifact_contracts(stage)}

## Required completion record
Before finishing, write `.appforge/{STAGE_RESULT_FILE_NAME}` matching this schema:
```json
{json.dumps(stage_result_schema, ensure_ascii=False, indent=2)}
```

## Repair rules
- Prefer the smallest targeted edit that resolves the actual failure.
- If a check failed, run or explain the exact check before claiming it passed.
- If you cannot execute a check, mark it as unverified rather than successful.
- Preserve unrelated user work and avoid rewriting the entire project unless `next_retry_mode` is `regenerate`.
"""

def build_stage_prompt(
    layout: ProjectLayout,
    *,
    project: dict[str, Any],
    pipeline: PipelineSpec,
    stage: StageSpec,
    attempt: int,
    previous_failure: dict[str, Any] | None = None,
) -> str:
    if previous_failure and previous_failure.get("repair_mode") in {"repair", "regenerate"}:
        return build_repair_stage_prompt(
            layout,
            project=project,
            pipeline=pipeline,
            stage=stage,
            attempt=attempt,
            previous_failure=previous_failure,
        )

    stage_skill = load_skill(stage.skill)
    reviewer = load_skill("meta/reviewer.md")
    autonomy = load_skill("meta/autonomous-execution.md")
    checkpoint = load_skill("meta/checkpoint-protocol.md")
    engineering_spine = load_skill("meta/engineering-spine.md")
    stage_result_schema = read_json(Path(__file__).resolve().parent / "resources" / "schemas" / "stage-result.schema.json", {})
    tool_info = {name: ToolRegistry().get(name).info() for name in stage.tools if name in ToolRegistry().names()}
    failure_section = ""
    if previous_failure:
        failure_section = (
            "\n## Previous attempt failed\n"
            "Do not repeat the same failure. Use the evidence below to target the repair before marking this stage complete.\n"
            f"```json\n{json.dumps(previous_failure, ensure_ascii=False, indent=2)}\n```\n"
        )

    return f"""# OpenAppForge stage execution packet

You are the implementation agent for one stage of a governed software-production pipeline.
Work directly in the current workspace. Do not merely describe the work: create or modify the files, run the checks, and leave the repository in a truthful state.

## Project
- Name: `{project['name']}`
- Original request: {project['prompt']}
- Pipeline: `{pipeline.name}` — {pipeline.description}
- Mode: `{project['mode']}`
- Network downloads allowed by runner: `{bool((project.get('safety') or {}).get('allow_network', False))}`
- Destructive AppForge tool operations allowed: `{bool((project.get('safety') or {}).get('allow_destructive', False))}`
- Stage: `{stage.name}` (attempt {attempt})
- Stage purpose: {stage.description}

## Non-negotiable operating contract
{autonomy}

## V5 agentic engineering chain
{engineering_spine}

## Stage director skill
{stage_skill}

## Review protocol
{reviewer}

## Checkpoint protocol
{checkpoint}

## Stage review focus
{chr(10).join(f'- {item}' for item in stage.review_focus) or '- Use the stage skill.'}

## Stage success criteria
{chr(10).join(f'- {item}' for item in stage.success_criteria) or '- Meet the artifact contract and leave no known blocker.'}

## Available AppForge tools
You may call these through `appforge tool run <name> --project . --input '<json>'` when useful.
```json
{json.dumps(tool_info, ensure_ascii=False, indent=2)}
```

## Relevant stack and domain knowledge
{_relevant_knowledge(layout, str(project['prompt']))}

## Existing workspace tree
{_workspace_tree(layout)}

## Relevant workspace file contents
{_relevant_file_contents(layout, stage, previous_failure=previous_failure)}

## Prior accepted artifacts
{_prior_artifacts(layout, pipeline, stage.name)}

## Persistent engineering memory
{render_memory_context(layout, current_stage=stage.name)}

## Required stage artifacts
{_artifact_contracts(stage)}

## Required completion record
Before finishing, write `.appforge/{STAGE_RESULT_FILE_NAME}` matching this schema:
```json
{json.dumps(stage_result_schema, ensure_ascii=False, indent=2)}
```
Set `stage` to `{stage.name}`. Use `status: "completed"` only after the actual work and checks are done. If a real blocker prevents completion, use `status: "blocked"`, document the exact blocker and the safest next action, and do not pretend success.

{failure_section}
## Final instruction
Execute this stage now. Preserve unrelated user work. Prefer the smallest coherent architecture that meets the product request. Do not wait for routine choices: make a reasonable, documented decision and continue. Never deploy, publish, purchase, expose credentials, or perform destructive repository operations without explicit permission.
"""
