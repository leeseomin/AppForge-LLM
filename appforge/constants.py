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

# Safety posture written into project.json and injected into every policy-aware tool.
# Dependency installation is on by default because the pipeline promises a tested app
# and every test/build gate needs a resolved toolchain; its writes stay in the workspace.
SAFETY_KEYS = ("allow_network", "allow_deploy", "allow_destructive", "allow_dependency_install")
DEFAULT_SAFETY = {
    "allow_network": False,
    "allow_deploy": False,
    "allow_destructive": False,
    "allow_dependency_install": True,
}

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    CONTROL_DIR_NAME,
    ".appforge-web",
    "projects",
    "openappforge.egg-info",
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
