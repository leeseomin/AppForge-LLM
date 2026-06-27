# Verification report

Date: 2026-06-27
Version: 0.4.0

## Commands executed

```bash
python -m compileall -q appforge tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m pip wheel . --no-deps --no-build-isolation -w dist
python -m pip install --no-deps --target /tmp/appforge_v4_smoke dist/openappforge-0.4.0-py3-none-any.whl
PYTHONPATH=/tmp/appforge_v4_smoke python - <<'PY'
import appforge
from appforge.pipelines import load_pipeline
print(appforge.__version__)
p = load_pipeline('web-app')
print(p.version)
print(' -> '.join(stage.name for stage in p.stages[:5]))
PY
```

## Results

- Python compilation completed successfully.
- Pytest completed with `21 passed`.
- Built wheel: `dist/openappforge-0.4.0-py3-none-any.whl`.
- Wheel smoke check loaded package version `0.4.0` and confirmed built-in pipeline version `1.1` with the v4 spine prefix: `intake -> specification -> workflow_design -> memory_engineering -> loop_engineering`.

## Notes

The default environment includes external pytest plugins that can keep the interpreter alive after pytest has printed the successful summary. Verification used `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` to run the repository tests in a deterministic plugin-free mode.
