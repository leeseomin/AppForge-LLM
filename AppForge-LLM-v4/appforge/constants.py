from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = PACKAGE_DIR / "resources"
PIPELINE_DIR = RESOURCE_DIR / "pipeline_defs"
SKILLS_DIR = RESOURCE_DIR / "skills"
SCHEMAS_DIR = RESOURCE_DIR / "schemas"
ARTIFACT_SCHEMAS_DIR = SCHEMAS_DIR / "artifacts"

CONTROL_DIR_NAME = ".appforge"
PROJECT_FILE_NAME = "project.json"
STATE_FILE_NAME = "state.json"
STAGE_RESULT_FILE_NAME = "stage-result.json"

DEFAULT_PROJECTS_DIR = Path("projects")
DEFAULT_MAX_STAGE_ATTEMPTS = 3
DEFAULT_COMMAND_TIMEOUT = 900
MAX_CAPTURE_CHARS = 40_000

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    CONTROL_DIR_NAME,
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    ".turbo",
    ".cache",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "target",
    "coverage",
}
