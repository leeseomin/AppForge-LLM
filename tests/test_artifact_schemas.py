from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from appforge.artifacts import validate_artifact
from appforge.constants import ARTIFACT_SCHEMAS_DIR
from appforge.gates import validate_stage_artifacts
from appforge.pipelines import load_pipeline
from appforge.projects import initialize_project
from appforge.util import atomic_write_json


def sample(schema: dict[str, Any]) -> Any:
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "oneOf" in schema:
        return sample(schema["oneOf"][0])
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        return {name: sample(properties.get(name, {})) for name in schema.get("required", [])}
    if kind == "array":
        return [sample(schema.get("items", {})) for _ in range(int(schema.get("minItems", 0)))]
    if kind == "boolean":
        return True
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "number":
        return max(1.0, float(schema.get("minimum", 1.0)))
    return "x" * max(1, int(schema.get("minLength", 1)))


def test_every_artifact_schema_is_well_formed_and_accepts_a_minimal_instance() -> None:
    paths = sorted(ARTIFACT_SCHEMAS_DIR.glob("*.schema.json"))
    assert len(paths) == 25
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        payload = sample(schema)
        name = path.name.removesuffix(".schema.json")
        validate_artifact(name, payload)


def _artifact_sample(name: str) -> dict[str, Any]:
    schema = json.loads(
        (ARTIFACT_SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
    )
    payload = sample(schema)
    assert isinstance(payload, dict)
    return payload


def test_semantic_gate_rejects_duplicate_and_dangling_traceability(
    tmp_path: Path,
) -> None:
    layout = initialize_project(
        "Build a traced prototype",
        projects_dir=tmp_path,
        name="semantic-gate-invalid",
        pipeline_name="prototype",
    )
    requirements = _artifact_sample("requirements_spec")
    requirements["functional_requirements"][0]["id"] = "REQ-001"
    requirements["non_functional_requirements"][0]["id"] = "REQ-001"
    requirements["traceability"][0] = {
        "from": "product-brief",
        "to": "REQ-001",
        "relationship": "defines",
    }
    atomic_write_json(layout.artifacts / "requirements_spec.json", requirements)

    requirements_ok, requirement_records, _paths = validate_stage_artifacts(
        layout,
        load_pipeline("prototype").stage("specification"),
    )
    assert requirements_ok is False
    assert "duplicate requirement id" in requirement_records[0]["error"]

    requirements["non_functional_requirements"][0]["id"] = "NFR-001"
    requirements["traceability"].append(
        {"from": "product-brief", "to": "NFR-001", "relationship": "defines"}
    )
    atomic_write_json(layout.artifacts / "requirements_spec.json", requirements)
    workflow = _artifact_sample("workflow_spec")
    workflow["steps"][0]["id"] = "STEP-001"
    workflow["traceability"][0] = {
        "requirement": "REQ-UNKNOWN",
        "workflow_step": "STEP-UNKNOWN",
    }
    atomic_write_json(layout.artifacts / "workflow_spec.json", workflow)

    workflow_ok, workflow_records, _paths = validate_stage_artifacts(
        layout,
        load_pipeline("prototype").stage("workflow_design"),
    )
    assert workflow_ok is False
    assert "unknown requirement" in workflow_records[0]["error"]
    assert "unknown workflow step" in workflow_records[0]["error"]


def test_semantic_gate_accepts_coherent_planning_chain(tmp_path: Path) -> None:
    layout = initialize_project(
        "Build a traced prototype",
        projects_dir=tmp_path,
        name="semantic-gate-valid",
        pipeline_name="prototype",
    )
    pipeline = load_pipeline("prototype")

    requirements = _artifact_sample("requirements_spec")
    requirements["functional_requirements"][0]["id"] = "FR-001"
    requirements["non_functional_requirements"][0]["id"] = "NFR-001"
    requirements["quality_gates"][0]["id"] = "QG-001"
    requirements["traceability"] = [
        {"from": "product-brief", "to": "FR-001", "relationship": "defines"},
        {"from": "product-brief", "to": "NFR-001", "relationship": "defines"},
    ]
    atomic_write_json(layout.artifacts / "requirements_spec.json", requirements)

    workflow = _artifact_sample("workflow_spec")
    workflow["steps"][0]["id"] = "STEP-001"
    workflow["traceability"] = [
        {"requirement": "FR-001", "workflow_step": "STEP-001"},
        {"requirement": "NFR-001", "workflow_step": "STEP-001"},
    ]
    atomic_write_json(layout.artifacts / "workflow_spec.json", workflow)

    memory = _artifact_sample("memory_spec")
    memory["memory_surfaces"][0]["id"] = "MEM-001"
    memory["traceability"] = [
        {"requirement": "FR-001", "memory_surface": "MEM-001"},
    ]
    atomic_write_json(layout.artifacts / "memory_spec.json", memory)

    loop = _artifact_sample("loop_spec")
    loop["loops"][0]["id"] = "LOOP-001"
    loop["traceability"] = [
        {"workflow_step": "STEP-001", "loop": "LOOP-001"},
    ]
    atomic_write_json(layout.artifacts / "loop_spec.json", loop)

    for stage_name in (
        "specification",
        "workflow_design",
        "memory_engineering",
        "loop_engineering",
    ):
        passed, records, _paths = validate_stage_artifacts(
            layout,
            pipeline.stage(stage_name),
        )
        assert passed is True, records
