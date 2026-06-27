from __future__ import annotations

from pathlib import Path
from typing import Any

from appforge.artifacts import ArtifactValidationError, validate_artifact_file
from appforge.models import ToolResult
from appforge.util import safe_resolve

from ..base import Tool


class ValidateArtifactTool(Tool):
    name = "validate_artifact"
    description = "Validate a stage artifact JSON file against its registered schema."
    capability = "governance"
    input_schema = {
        "type": "object",
        "required": ["name", "path"],
        "properties": {"name": {"type": "string"}, "path": {"type": "string"}},
    }

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        path = safe_resolve(workspace, str(inputs["path"]))
        try:
            payload = validate_artifact_file(str(inputs["name"]), path)
        except ArtifactValidationError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"valid": True, "keys": sorted(payload), "path": path.relative_to(workspace).as_posix()})
