"""Deterministic fixture agent used to exercise the complete runner without an LLM."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def sample(schema: dict[str, Any], name: str = "value") -> Any:
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "oneOf" in schema:
        return sample(schema["oneOf"][0], name)
    if "anyOf" in schema:
        return sample(schema["anyOf"][0], name)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), kind[0])
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {key: sample(props.get(key, {}), key) for key in schema.get("required", [])}
    if kind == "array":
        count = max(int(schema.get("minItems", 0)), 1 if schema.get("minItems", 0) else 0)
        return [sample(schema.get("items", {}), name) for _ in range(count)]
    if kind == "boolean":
        return True
    if kind == "integer":
        return max(int(schema.get("minimum", 1)), 1)
    if kind == "number":
        return max(float(schema.get("minimum", 1)), 1.0)
    min_length = max(int(schema.get("minLength", 1)), 1)
    value = f"fixture-{name}"
    return value if len(value) >= min_length else value + ("x" * (min_length - len(value)))


def cohere_planning_artifact(
    workspace: Path,
    artifact: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if artifact == "requirements_spec":
        requirement_ids: list[str] = []
        for index, item in enumerate(payload["functional_requirements"], start=1):
            item["id"] = f"FR-{index:03d}"
            requirement_ids.append(item["id"])
        for index, item in enumerate(payload["non_functional_requirements"], start=1):
            item["id"] = f"NFR-{index:03d}"
            requirement_ids.append(item["id"])
        for index, item in enumerate(payload["quality_gates"], start=1):
            item["id"] = f"QG-{index:03d}"
        for index, item in enumerate(payload["risks"], start=1):
            item["id"] = f"RISK-{index:03d}"
        payload["traceability"] = [
            {
                "from": "product_brief",
                "to": requirement_id,
                "relationship": "defines",
            }
            for requirement_id in requirement_ids
        ]
    elif artifact == "workflow_spec":
        for index, item in enumerate(payload["steps"], start=1):
            item["id"] = f"STEP-{index:03d}"
        requirements = json.loads(
            (workspace / ".appforge" / "artifacts" / "requirements_spec.json").read_text(
                encoding="utf-8"
            )
        )
        requirement_ids = [
            item["id"]
            for key in ("functional_requirements", "non_functional_requirements")
            for item in requirements[key]
        ]
        payload["traceability"] = [
            {
                "requirement": requirement_id,
                "workflow_step": payload["steps"][0]["id"],
            }
            for requirement_id in requirement_ids
        ]
    elif artifact == "memory_spec":
        for index, item in enumerate(payload["memory_surfaces"], start=1):
            item["id"] = f"MEM-{index:03d}"
        requirements = json.loads(
            (workspace / ".appforge" / "artifacts" / "requirements_spec.json").read_text(
                encoding="utf-8"
            )
        )
        requirement_id = requirements["functional_requirements"][0]["id"]
        payload["traceability"] = [
            {
                "requirement": requirement_id,
                "memory_surface": item["id"],
            }
            for item in payload["memory_surfaces"]
        ]
    elif artifact == "loop_spec":
        for index, item in enumerate(payload["loops"], start=1):
            item["id"] = f"LOOP-{index:03d}"
        workflow = json.loads(
            (workspace / ".appforge" / "artifacts" / "workflow_spec.json").read_text(
                encoding="utf-8"
            )
        )
        payload["traceability"] = [
            {
                "workflow_step": workflow["steps"][0]["id"],
                "loop": item["id"],
            }
            for item in payload["loops"]
        ]
    return payload


def write_fixture_app(workspace: Path) -> None:
    (workspace / "src").mkdir(exist_ok=True)
    (workspace / "test").mkdir(exist_ok=True)
    (workspace / "src" / "index.js").write_text(
        "function add(left, right) { return left + right; }\nmodule.exports = { add };\n",
        encoding="utf-8",
    )
    (workspace / "test" / "app.test.js").write_text(
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        "const { add } = require('../src/index.js');\n"
        "test('add', () => assert.equal(add(2, 3), 5));\n",
        encoding="utf-8",
    )
    package = {
        "name": "openappforge-fixture-app",
        "version": "0.0.1",
        "private": True,
        "scripts": {
            "test": "node --test",
            "build": (
                "node -e \"const fs=require('fs');"
                "fs.mkdirSync('dist',{recursive:true});"
                "fs.copyFileSync('src/index.js','dist/index.js')\""
            ),
        },
    }
    (workspace / "package.json").write_text(
        json.dumps(package, indent=2) + "\n", encoding="utf-8"
    )
    (workspace / "README.md").write_text(
        "# Fixture app\n\nRun `npm test` and `npm run build`.\n", encoding="utf-8"
    )
    (workspace / "LICENSE").write_text("Test fixture license.\n", encoding="utf-8")


def main() -> int:
    # Consume the packet so the parent never encounters a closed stdin pipe.
    sys.stdin.read()
    workspace = Path(sys.argv[1]).resolve()
    stage = sys.argv[2]
    framework_root = Path(sys.argv[3]).resolve()
    stage_to_artifact = {
        "intake": "product_brief",
        "specification": "requirements_spec",
        "workflow_design": "workflow_spec",
        "memory_engineering": "memory_spec",
        "loop_engineering": "loop_spec",
        "prototype_plan": "prototype_plan",
        "architecture": "architecture_spec",
        "experience": "experience_spec",
        "implementation": "implementation_report",
        "verification": "verification_report",
        "security": "security_report",
        "release": "release_report",
        "demo": "demo_report",
        "handoff": "handoff_report",
    }
    artifact = stage_to_artifact[stage]
    if stage == "implementation":
        write_fixture_app(workspace)

    schema_path = framework_root / "appforge" / "resources" / "schemas" / "artifacts" / f"{artifact}.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    payload = sample(schema, artifact)
    payload = cohere_planning_artifact(workspace, artifact, payload)
    artifact_dir = workspace / ".appforge" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"{artifact}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result = {
        "stage": stage,
        "status": "completed",
        "summary": f"Fixture completed {stage}",
        "files_changed": [f".appforge/artifacts/{artifact}.json"],
        "commands_run": [],
        "checks": [{"name": "fixture", "passed": True, "evidence": "deterministic fixture"}],
        "decisions": [{"decision": "Use fixture output", "reason": "runner integration test"}],
        "unresolved": [],
    }
    (workspace / ".appforge" / "stage-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"completed {stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
