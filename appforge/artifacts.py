from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

from .constants import ARTIFACT_SCHEMAS_DIR


class ArtifactValidationError(ValueError):
    pass


@lru_cache(maxsize=128)
def load_artifact_schema(name: str) -> dict[str, Any]:
    path = ARTIFACT_SCHEMAS_DIR / f"{name}.schema.json"
    if not path.exists():
        raise ArtifactValidationError(f"No schema registered for artifact {name!r}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_artifact(name: str, payload: dict[str, Any]) -> None:
    try:
        jsonschema.validate(payload, load_artifact_schema(name))
    except jsonschema.ValidationError as exc:
        where = ".".join(str(part) for part in exc.absolute_path)
        suffix = f" at {where}" if where else ""
        raise ArtifactValidationError(f"{name}{suffix}: {exc.message}") from exc


def validate_artifact_file(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ArtifactValidationError(f"Missing artifact file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ArtifactValidationError(f"Artifact {path} must contain a JSON object")
    validate_artifact(name, payload)
    return payload
