# Extending OpenAppForge

## Add a pipeline

Create `appforge/resources/pipeline_defs/<name>.yaml` and satisfy `resources/schemas/pipeline.schema.json`. Reuse existing stages where possible. A production pipeline should normally cover intake, specification, architecture/design, implementation, verification, security, release, and handoff.

Every produced artifact name must have a schema under `resources/schemas/artifacts/`. Every gate tool must exist in the live tool registry. Run the pipeline invariant tests after adding the manifest.

## Add a skill

Skills are Markdown operating procedures under:

- `skills/meta/` for global behavior;
- `skills/stages/` for stage execution;
- `skills/stacks/` for language/framework guidance;
- `skills/domains/` for cross-cutting product concerns.

A useful skill states inputs, work sequence, decision rules, evidence, failure conditions, and completion criteria. It should not repeat schemas or encode provider-specific API calls when a tool contract is more appropriate.

## Add an artifact contract

Create `<artifact>.schema.json` using JSON Schema 2020-12. Require `schema_version` with value `1.0`, require meaningful evidence fields, and avoid free-form blobs when stable identifiers or lists are possible.

Schemas are review boundaries. Make false completion difficult: verification artifacts should contain commands and evidence; security artifacts should contain findings and residual risk; release artifacts should contain reproducibility and rollback information.

## Add a tool

Create a module under `appforge/tooling/tools/`:

```python
from pathlib import Path
from typing import Any

from appforge.models import ToolResult
from appforge.tooling.base import Tool


class ExampleTool(Tool):
    name = "example"
    description = "Perform one bounded, deterministic operation."
    capability = "verification"
    network_required = False
    destructive = False
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
    }

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data={"echo": inputs.get("value", "")})
```

The registry auto-discovers concrete `Tool` subclasses with a class-local unique `name`. Return a structured `ToolResult`; do not throw expected operational failures. Declare network/destructive properties truthfully and enforce narrower local rules inside the tool when needed.

## Add a driver

Subclass `AgentDriver` in `drivers.py`. A driver receives the complete stage packet, project layout, stage, attempt, and timeout. It must:

- run only in `layout.root`;
- use non-shell argument execution;
- capture bounded/redacted output;
- return a `DriverResult`;
- leave artifact and completion validation to the orchestrator.

Do not treat a model's final message as proof of stage completion.

## Compatibility checks

Run:

```bash
pytest
python -m appforge pipelines
python -m appforge tool list
python -m build
```

Inspect the built wheel to ensure new resource files are included.
