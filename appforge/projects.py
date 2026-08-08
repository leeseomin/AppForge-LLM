from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import DEFAULT_SAFETY, PROJECT_FILE_NAME, SAFETY_KEYS, STATE_FILE_NAME
from .models import ProjectLayout
from .pipelines import auto_select_pipeline, load_pipeline
from .util import atomic_write_json, atomic_write_text, read_json, slugify, utc_now


def resolve_safety(overrides: dict[str, Any] | None = None) -> dict[str, bool]:
    """Merge caller overrides onto the default safety posture, ignoring unknown keys."""
    safety = dict(DEFAULT_SAFETY)
    for key in SAFETY_KEYS:
        if overrides and key in overrides:
            safety[key] = bool(overrides[key])
    return safety


class ProjectError(ValueError):
    pass


def initialize_project(
    prompt: str,
    *,
    projects_dir: Path,
    name: str | None = None,
    pipeline_name: str = "auto",
    mode: str | None = None,
    existing_target: Path | None = None,
    safety: dict[str, Any] | None = None,
) -> ProjectLayout:
    if not prompt.strip():
        raise ProjectError("The product request cannot be empty")
    if existing_target is not None:
        root = existing_target.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if (root / ".appforge" / PROJECT_FILE_NAME).exists():
            raise FileExistsError(f"OpenAppForge is already initialized at {root}; use appforge run to resume")
        existing_repo = any(root.iterdir())
    else:
        derived = name or slugify(prompt[:72])
        root = (projects_dir / slugify(derived)).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=False)
        existing_repo = False

    layout = ProjectLayout.from_root(root)
    for directory in (
        layout.control,
        layout.artifacts,
        layout.checkpoints,
        layout.prompts,
        layout.logs,
        layout.reports,
        layout.memory,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    if pipeline_name == "auto":
        selected, scores = auto_select_pipeline(prompt, existing_repo=existing_repo)
    else:
        selected, scores = pipeline_name, {}
    pipeline = load_pipeline(selected)
    selected_mode = mode or pipeline.default_mode
    if selected_mode not in {"autonomous", "guided"}:
        raise ProjectError("mode must be 'autonomous' or 'guided'")

    project = {
        "version": "1.0",
        "name": root.name,
        "root": str(root),
        "prompt": prompt,
        "pipeline": selected,
        "pipeline_version": pipeline.version,
        "mode": selected_mode,
        "created_at": utc_now(),
        "existing_repository": existing_repo,
        "selection_scores": scores,
        "safety": resolve_safety(safety),
    }
    atomic_write_json(layout.control / PROJECT_FILE_NAME, project)
    atomic_write_json(
        layout.control / STATE_FILE_NAME,
        {
            "version": "1.0",
            "status": "initialized",
            "current_stage": pipeline.stages[0].name,
            "completed_stages": [],
            "updated_at": utc_now(),
        },
    )
    atomic_write_text(
        layout.control / "request.md",
        f"# Product request\n\n{prompt.strip()}\n\n"
        f"- Pipeline: `{selected}`\n- Mode: `{selected_mode}`\n",
    )
    gitignore = root / ".gitignore"
    managed_entries = [
        ".appforge/logs/",
        ".appforge/prompts/",
        ".appforge/reports/",
        ".appforge/memory/",
        ".appforge/stage-result.json",
        ".env",
        ".env.*",
        "!.env.example",
    ]
    if gitignore.exists():
        current = gitignore.read_text(encoding="utf-8", errors="replace")
        missing = [entry for entry in managed_entries if entry not in current.splitlines()]
        if missing:
            suffix = "\n" if current and not current.endswith("\n") else ""
            atomic_write_text(
                gitignore,
                current + suffix + "\n# OpenAppForge generated/runtime files\n" + "\n".join(missing) + "\n",
            )
    else:
        atomic_write_text(
            gitignore,
            "# OpenAppForge generated/runtime files\n"
            + "\n".join(managed_entries)
            + "\nnode_modules/\n.venv/\n__pycache__/\ndist/\nbuild/\ncoverage/\n",
        )
    return layout


def find_project(start: Path) -> ProjectLayout:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".appforge" / PROJECT_FILE_NAME).exists():
            return ProjectLayout.from_root(candidate)
    raise ProjectError(f"No OpenAppForge project found from {start}")


def load_project(layout: ProjectLayout) -> dict[str, Any]:
    data = read_json(layout.control / PROJECT_FILE_NAME)
    if not isinstance(data, dict):
        raise ProjectError(f"Invalid project metadata at {layout.control / PROJECT_FILE_NAME}")
    # Projects created before a safety key existed must still get a defined posture
    # rather than silently falling back to "denied" for every policy-aware tool.
    stored = data.get("safety") if isinstance(data.get("safety"), dict) else {}
    data["safety"] = resolve_safety(stored)
    return data


def update_project(layout: ProjectLayout, updates: dict[str, Any]) -> dict[str, Any]:
    data = load_project(layout)
    data.update(updates)
    atomic_write_json(layout.control / PROJECT_FILE_NAME, data)
    return data
