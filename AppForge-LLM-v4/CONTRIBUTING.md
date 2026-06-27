# Contributing

Changes should preserve four invariants: stages are resumable, required evidence is machine-validated, dangerous capabilities are explicit, and completion claims are truthful.

Before submitting a change:

```bash
python -m pip install -e '.[dev]'
pytest
python -m build
```

Add tests for pipeline routing, new schemas, tools, safety boundaries, and checkpoint behavior. Avoid provider-specific logic in skills when a driver or tool is the correct extension point. Never commit credentials or generated project workspaces.
