from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import load_artifact_schema
from .constants import SKILLS_DIR, STAGE_RESULT_FILE_NAME
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
            chunks.append(f"### {artifact}\n```json\n{truncate(text, 12_000)}\n```")
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


def build_stage_prompt(
    layout: ProjectLayout,
    *,
    project: dict[str, Any],
    pipeline: PipelineSpec,
    stage: StageSpec,
    attempt: int,
    previous_failure: dict[str, Any] | None = None,
) -> str:
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
            "Do not repeat the same failure. Fix every required item below before marking this stage complete.\n"
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

## V4 hardened engineering chain
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
