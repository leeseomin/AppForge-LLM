from __future__ import annotations

import json
from typing import Any

import jsonschema

from .artifacts import ArtifactValidationError, validate_artifact_file
from .constants import SCHEMAS_DIR, STAGE_RESULT_FILE_NAME
from .models import ProjectLayout, StageSpec
from .tooling.registry import ToolRegistry
from .util import read_json


def validate_stage_result(layout: ProjectLayout, stage: StageSpec) -> tuple[bool, dict[str, Any], str | None]:
    path = layout.control / STAGE_RESULT_FILE_NAME
    if not path.exists():
        return False, {}, f"Missing completion record: {path.relative_to(layout.root)}"
    try:
        data = read_json(path)
        if not isinstance(data, dict):
            return False, {}, "stage-result.json must contain a JSON object"
        schema = read_json(SCHEMAS_DIR / "stage-result.schema.json")
        jsonschema.validate(data, schema)
    except (json.JSONDecodeError, jsonschema.ValidationError, OSError) as exc:
        return False, {}, f"Invalid stage-result.json: {exc}"
    if data.get("stage") != stage.name:
        return False, data, f"stage-result.json stage is {data.get('stage')!r}, expected {stage.name!r}"
    if data.get("status") != "completed":
        blockers = data.get("unresolved") or []
        return False, data, f"Agent reported {data.get('status')!r}; blockers: {blockers}"
    return True, data, None


def validate_stage_artifacts(layout: ProjectLayout, stage: StageSpec) -> tuple[bool, list[dict[str, Any]], dict[str, str]]:
    results: list[dict[str, Any]] = []
    artifact_paths: dict[str, str] = {}
    passed = True
    for artifact in stage.produces:
        path = layout.artifacts / f"{artifact}.json"
        try:
            validate_artifact_file(artifact, path)
            results.append({"kind": "artifact", "name": artifact, "required": True, "passed": True, "path": str(path)})
            artifact_paths[artifact] = str(path)
        except ArtifactValidationError as exc:
            passed = False
            results.append({"kind": "artifact", "name": artifact, "required": True, "passed": False, "error": str(exc), "path": str(path)})
    return passed, results, artifact_paths


def run_declared_gates(
    layout: ProjectLayout,
    *,
    stage: StageSpec,
    allow_network: bool,
    allow_destructive: bool,
) -> tuple[bool, list[dict[str, Any]]]:
    registry = ToolRegistry()
    all_passed = True
    records: list[dict[str, Any]] = []
    for gate in stage.gates:
        try:
            tool = registry.get(gate.tool)
            inputs = dict(gate.inputs)
            if tool.network_required and not allow_network:
                skipped_record = {
                    "success": True,
                    "data": {"skipped": True, "reason": "Network access is disabled; rerun with --allow-network"},
                    "artifacts": [],
                    "error": None,
                    "duration_seconds": 0.0,
                    "command": None,
                }
                if gate.required:
                    all_passed = False
                records.append(
                    {
                        "kind": "tool",
                        "name": gate.tool,
                        "required": gate.required,
                        "passed": not gate.required,
                        "skipped": True,
                        "result": skipped_record,
                    }
                )
                continue
            if tool.network_required:
                inputs.setdefault("allow_network", True)
            if tool.destructive:
                inputs.setdefault("allow_destructive", allow_destructive)
            result = tool.run(layout.root, inputs)
            skipped = bool(result.data.get("skipped", False))
            passed = result.success and not (gate.required and skipped)
            if gate.required and not passed:
                all_passed = False
            records.append(
                {
                    "kind": "tool",
                    "name": gate.tool,
                    "required": gate.required,
                    "passed": passed if gate.required else (result.success or skipped),
                    "skipped": skipped,
                    "result": result.to_dict(),
                }
            )
        except Exception as exc:
            if gate.required:
                all_passed = False
            records.append({"kind": "tool", "name": gate.tool, "required": gate.required, "passed": False, "error": f"{type(exc).__name__}: {exc}"})
    return all_passed, records


def review_stage(stage: StageSpec, records: list[dict[str, Any]], stage_result: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for record in records:
        if record.get("required") and not record.get("passed"):
            detail = record.get("error") or (record.get("result") or {}).get("error") or "Required gate failed"
            findings.append(
                {
                    "severity": "critical",
                    "criterion": record.get("name"),
                    "finding": detail,
                    "proposed_fix": f"Correct the {record.get('name')} failure, rerun it, and update the stage artifact truthfully.",
                }
            )
        elif not record.get("passed"):
            detail = record.get("error") or (record.get("result") or {}).get("error") or "Optional gate did not pass"
            findings.append(
                {
                    "severity": "suggestion",
                    "criterion": record.get("name"),
                    "finding": detail,
                    "proposed_change": "Run or repair this optional check when the environment supports it, and document the limitation now.",
                }
            )
        elif record.get("skipped"):
            findings.append(
                {
                    "severity": "suggestion",
                    "criterion": record.get("name"),
                    "finding": "Optional gate was skipped because no compatible command or dependency was available.",
                    "proposed_change": "Document the missing check in the verification or handoff report.",
                }
            )
    unresolved = stage_result.get("unresolved") or []
    for item in unresolved:
        findings.append(
            {
                "severity": "suggestion",
                "criterion": "unresolved",
                "finding": str(item),
                "proposed_change": "Resolve it now or document why it is safely deferred with an owner and verification step.",
            }
        )
    critical = sum(1 for item in findings if item["severity"] == "critical")
    return {
        "passed": critical == 0,
        "critical_count": critical,
        "finding_count": len(findings),
        "review_focus": list(stage.review_focus),
        "findings": findings,
    }
