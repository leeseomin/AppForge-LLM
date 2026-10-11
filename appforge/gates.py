from __future__ import annotations

import json
import shlex
from typing import Any

import jsonschema

from .artifacts import ArtifactValidationError, validate_artifact_file
from .constants import SCHEMAS_DIR, STAGE_RESULT_FILE_NAME
from .models import ProjectLayout, StageSpec
from .tooling.registry import ToolRegistry
from .util import read_json


def _object_items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _unique_ids(
    items: list[dict[str, Any]],
    *,
    field: str,
    label: str,
    errors: list[str],
) -> set[str]:
    identifiers: set[str] = set()
    for index, item in enumerate(items):
        identifier = str(item.get(field) or "").strip()
        if not identifier:
            errors.append(f"{label}[{index}] has no {field}")
        elif identifier in identifiers:
            errors.append(f"duplicate {label} id {identifier!r}")
        else:
            identifiers.add(identifier)
    return identifiers


def _read_artifact_object(
    layout: ProjectLayout,
    name: str,
    errors: list[str],
) -> dict[str, Any]:
    payload = read_json(layout.artifacts / f"{name}.json")
    if not isinstance(payload, dict):
        errors.append(f"required upstream artifact {name!r} is missing or invalid")
        return {}
    return payload


def _requirement_ids(payload: dict[str, Any], errors: list[str]) -> set[str]:
    requirements = [
        *_object_items(payload, "functional_requirements"),
        *_object_items(payload, "non_functional_requirements"),
    ]
    return _unique_ids(
        requirements,
        field="id",
        label="requirement",
        errors=errors,
    )


def _requirements_semantics(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    requirement_ids = _requirement_ids(payload, errors)
    _unique_ids(
        _object_items(payload, "quality_gates"),
        field="id",
        label="quality gate",
        errors=errors,
    )
    _unique_ids(
        _object_items(payload, "risks"),
        field="id",
        label="risk",
        errors=errors,
    )
    traced: set[str] = set()
    for index, link in enumerate(_object_items(payload, "traceability")):
        endpoints = {
            str(link.get("from") or "").strip(),
            str(link.get("to") or "").strip(),
        }
        referenced = endpoints & requirement_ids
        if not referenced:
            errors.append(
                f"traceability[{index}] references no known requirement id"
            )
        traced.update(referenced)
    for identifier in sorted(requirement_ids - traced):
        errors.append(f"requirement {identifier!r} has no traceability link")
    return errors


def _workflow_semantics(layout: ProjectLayout, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    requirements = _read_artifact_object(layout, "requirements_spec", errors)
    requirement_ids = _requirement_ids(requirements, errors)
    step_ids = _unique_ids(
        _object_items(payload, "steps"),
        field="id",
        label="workflow step",
        errors=errors,
    )
    traced_requirements: set[str] = set()
    traced_steps: set[str] = set()
    for index, link in enumerate(_object_items(payload, "traceability")):
        requirement = str(link.get("requirement") or "").strip()
        workflow_step = str(link.get("workflow_step") or "").strip()
        if requirement not in requirement_ids:
            errors.append(
                f"traceability[{index}] references unknown requirement {requirement!r}"
            )
        else:
            traced_requirements.add(requirement)
        if workflow_step not in step_ids:
            errors.append(
                f"traceability[{index}] references unknown workflow step {workflow_step!r}"
            )
        else:
            traced_steps.add(workflow_step)
    for identifier in sorted(requirement_ids - traced_requirements):
        errors.append(f"requirement {identifier!r} is not mapped to a workflow step")
    for identifier in sorted(step_ids - traced_steps):
        errors.append(f"workflow step {identifier!r} is not mapped to a requirement")
    return errors


def _memory_semantics(layout: ProjectLayout, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    requirements = _read_artifact_object(layout, "requirements_spec", errors)
    requirement_ids = _requirement_ids(requirements, errors)
    surface_ids = _unique_ids(
        _object_items(payload, "memory_surfaces"),
        field="id",
        label="memory surface",
        errors=errors,
    )
    traced_surfaces: set[str] = set()
    for index, link in enumerate(_object_items(payload, "traceability")):
        requirement = str(link.get("requirement") or "").strip()
        surface = str(link.get("memory_surface") or "").strip()
        if requirement not in requirement_ids:
            errors.append(
                f"traceability[{index}] references unknown requirement {requirement!r}"
            )
        if surface not in surface_ids:
            errors.append(
                f"traceability[{index}] references unknown memory surface {surface!r}"
            )
        else:
            traced_surfaces.add(surface)
    for identifier in sorted(surface_ids - traced_surfaces):
        errors.append(f"memory surface {identifier!r} has no requirement trace")
    return errors


def _loop_semantics(layout: ProjectLayout, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    workflow = _read_artifact_object(layout, "workflow_spec", errors)
    step_ids = _unique_ids(
        _object_items(workflow, "steps"),
        field="id",
        label="workflow step",
        errors=errors,
    )
    loop_ids = _unique_ids(
        _object_items(payload, "loops"),
        field="id",
        label="loop",
        errors=errors,
    )
    traced_loops: set[str] = set()
    for index, link in enumerate(_object_items(payload, "traceability")):
        workflow_step = str(link.get("workflow_step") or "").strip()
        loop = str(link.get("loop") or "").strip()
        if workflow_step not in step_ids:
            errors.append(
                f"traceability[{index}] references unknown workflow step {workflow_step!r}"
            )
        if loop not in loop_ids:
            errors.append(f"traceability[{index}] references unknown loop {loop!r}")
        else:
            traced_loops.add(loop)
    for identifier in sorted(loop_ids - traced_loops):
        errors.append(f"loop {identifier!r} has no workflow-step trace")
    return errors


def validate_artifact_semantics(
    layout: ProjectLayout,
    name: str,
    payload: dict[str, Any],
) -> list[str]:
    if name == "requirements_spec":
        return _requirements_semantics(payload) + _browser_contract_errors(layout, payload)
    if name == "workflow_spec":
        return _workflow_semantics(layout, payload)
    if name == "memory_spec":
        return _memory_semantics(layout, payload)
    if name == "loop_spec":
        return _loop_semantics(layout, payload)
    if name == "engineering_spec":
        errors: list[str] = []
        requirements = _object_items(payload, "requirements")
        _unique_ids(requirements, field="id", label="requirement", errors=errors)
        for item in requirements:
            if not item.get("acceptance") or any(not str(value).strip() for value in item["acceptance"]):
                errors.append(f"requirement {item.get('id')} has empty acceptance criteria")
        return errors + _browser_contract_errors(layout, payload)
    if name in {"verification_report", "regression_report"}:
        return verification_report_errors(layout, payload)
    return []


def _browser_contract_errors(layout: ProjectLayout, payload: dict[str, Any]) -> list[str]:
    from .browser_checks import contract_errors
    from .projects import load_project
    project = load_project(layout)
    if project.get("pipeline") not in {"web-app", "web-app-lite"}:
        return []
    errors = contract_errors(payload)
    if payload.get("verification_profile") not in {"static-web", "javascript-web", "typescript-web"}:
        errors.append("Accepted web spec must declare verification_profile")
    return errors


def verification_report_errors(layout: ProjectLayout, payload: dict[str, Any]) -> list[str]:
    """Completion semantics; failed reports remain valid, writable artifacts."""
    errors: list[str] = []
    if payload.get("passed") is not True:
        errors.append("Verification did not pass")
    if payload.get("failures"):
        errors.append("Verification contains unresolved failures")
    from .projects import load_project
    if load_project(layout).get("pipeline") in {"web-app", "web-app-lite"}:
        return errors + _verified_web_report_errors(layout, payload)
    for key in ("results", "acceptance_results"):
        for index, item in enumerate(_object_items(payload, key)):
            value = item.get("passed")
            if value is not True and value not in ("true", "passed"):
                errors.append(f"{key}[{index}] is failed or unverified")
    if "acceptance_results" in payload:
        spec = read_json(layout.artifacts / "engineering_spec.json")
        if isinstance(spec, dict):
            required = _unique_ids(_object_items(spec, "requirements"), field="id", label="requirement", errors=errors)
        else:
            spec = _read_artifact_object(layout, "requirements_spec", errors)
            required = _requirement_ids(spec, errors)
        reported = _unique_ids(_object_items(payload, "acceptance_results"), field="requirement",
                               label="acceptance result", errors=errors)
        for identifier in sorted(reported - required):
            errors.append(f"unknown requirement {identifier!r}")
        for identifier in sorted(required - reported):
            errors.append(f"missing requirement result {identifier!r}")
    return errors


def _verified_web_report_errors(layout: ProjectLayout, payload: dict[str, Any]) -> list[str]:
    from .evidence import read_host
    from .validation_service import current_receipt

    if payload.get("schema_version") != "2.0":
        return ["New web verification requires report schema_version 2.0 and current host execution IDs"]
    receipt = current_receipt(layout)
    if not receipt:
        return ["Run validate_candidate successfully on the current source before submitting verification"]
    errors: list[str] = []
    supplied = payload.get("execution_ids", [])
    required_ids = set(supplied)
    if not required_ids or len(supplied) != len(required_ids):
        return ["Report requires unique host execution IDs"]
    executions = {identifier: read_host(layout.root, identifier + ".json") for identifier in required_ids}
    for record in executions.values():
        if (not record or record.get("status") != "passed"
                or any(record.get(key) != receipt.get(key) for key in ("input_snapshot_id", "requirements_digest", "policy_digest"))):
            return ["Report execution IDs do not match the current source, requirements and policy"]
    required_gates = {record["name"] for record in receipt["records"]}
    if {record["gate_id"] for record in executions.values()} != required_gates:
        errors.append("Report execution IDs must cover every required candidate gate")
    commands = {record["gate_id"] for record in executions.values() if record}
    commands.update(shlex.join(record["result"]["command"]) for record in executions.values()
                    if record and record["result"].get("command"))
    for command in payload.get("test_commands", []):
        try:
            canonical = shlex.join(shlex.split(command))
        except ValueError:
            canonical = ""
        if canonical not in commands:
            errors.append(f"Command has no current execution evidence: {command}")
    expected = {item["criterion_id"] for item in receipt["acceptance_results"]}
    reported = _unique_ids(_object_items(payload, "acceptance_results"), field="criterion_id", label="criterion", errors=errors)
    if reported != expected:
        errors.append("Acceptance criterion IDs must exactly match the host browser results")
    for item in _object_items(payload, "results") + _object_items(payload, "acceptance_results"):
        if item.get("passed") is not True:
            errors.append("Report v2 results require boolean true backed by host evidence")
        references = item.get("evidence_ids", [])
        if not references or not set(references).issubset(required_ids):
            errors.append("Every passing result requires current host evidence_ids")
            continue
        criterion = item.get("criterion_id")
        if criterion:
            if item.get("requirement") != criterion.rsplit("/AC-", 1)[0]:
                errors.append(f"Requirement does not own criterion {criterion}")
            observed = {case["criterion_id"] for identifier in references
                        for case in (executions[identifier]["result"]["data"].get("acceptance_results") or [])
                        if case.get("status") == "passed"}
            if criterion not in observed:
                errors.append(f"Evidence does not observe criterion {criterion}")
    return errors


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
            payload = validate_artifact_file(artifact, path)
            semantic_errors = validate_artifact_semantics(layout, artifact, payload)
            if semantic_errors:
                raise ArtifactValidationError(
                    f"{artifact} semantic validation failed: "
                    + "; ".join(semantic_errors)
                )
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
    allow_dependency_install: bool = True,
) -> tuple[bool, list[dict[str, Any]]]:
    from .validation_service import WEB_PIPELINES, validate_candidate
    from .projects import load_project
    project = load_project(layout)
    if project.get("pipeline") in WEB_PIPELINES and stage.name in {"implementation", "verification"}:
        candidate = validate_candidate(layout)
        return bool(candidate["passed"]), candidate["records"]
    registry = ToolRegistry()
    all_passed = True
    records: list[dict[str, Any]] = []
    for gate in stage.gates:
        try:
            tool = registry.get(gate.tool)
            inputs = dict(gate.inputs)
            for key in getattr(tool, "policy_inputs", ()) or ():
                inputs.setdefault(
                    key,
                    {
                        "allow_network": allow_network,
                        "allow_destructive": allow_destructive,
                        "allow_dependency_install": allow_dependency_install,
                    }.get(key, False),
                )
            if getattr(tool, "dependency_install_required", False):
                inputs.setdefault("allow_dependency_install", allow_dependency_install)
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
        passed_value = record.get("passed")
        if passed_value is None:
            findings.append(
                {
                    "severity": "suggestion",
                    "criterion": record.get("name"),
                    "finding": record.get("evidence")
                    or "This check is an unverified self-report; no independent behavioral gate executed.",
                    "proposed_change": "Run an independent tool, test, or review pass before treating this check as verified.",
                }
            )
        elif record.get("required") and passed_value is False:
            detail = record.get("error") or (record.get("result") or {}).get("error") or "Required gate failed"
            findings.append(
                {
                    "severity": "critical",
                    "criterion": record.get("name"),
                    "finding": detail,
                    "proposed_fix": f"Correct the {record.get('name')} failure, rerun it, and update the stage artifact truthfully.",
                }
            )
        elif passed_value is False:
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
    for check in stage_result.get("checks") or []:
        if isinstance(check, dict) and check.get("passed") is None:
            findings.append(
                {
                    "severity": "suggestion",
                    "criterion": check.get("name") or "stage_result_check",
                    "finding": check.get("evidence")
                    or "Completion record contains an unverified self-report check.",
                    "proposed_change": "Show this as unverified in the UI and back it with an independent gate when possible.",
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
