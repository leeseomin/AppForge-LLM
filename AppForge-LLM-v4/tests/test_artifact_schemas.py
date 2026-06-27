from __future__ import annotations

import json
from typing import Any

import jsonschema

from appforge.artifacts import validate_artifact
from appforge.constants import ARTIFACT_SCHEMAS_DIR


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
    assert len(paths) == 24
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        payload = sample(schema)
        name = path.name.removesuffix(".schema.json")
        validate_artifact(name, payload)
