"""Permanent regressions for completion, evidence, and repair integrity."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from appforge import llm_bridge
from appforge.checkpoints import next_stage, read_checkpoint, write_checkpoint
from appforge.constants import ARTIFACT_SCHEMAS_DIR
from appforge.drivers import AgentDriver, DriverError, LLMBridgeAgentDriver, _apply_bridge_envelope
from appforge.gates import run_declared_gates, validate_stage_artifacts
from appforge.memory import failure_signature
from appforge.models import DriverResult, GateSpec, StageSpec, ToolResult
from appforge.pipelines import load_pipeline
from appforge.projects import initialize_project, load_project
from appforge.runner import PipelineRunner
from appforge.tooling.tools import quality
from appforge.tooling.tools.release import ReleaseReadinessTool
from appforge.util import atomic_write_json


def sample(schema):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "oneOf" in schema:
        return sample(schema["oneOf"][0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = kind[0]
    if kind == "object" or "properties" in schema:
        return {key: sample(schema.get("properties", {}).get(key, {}))
                for key in schema.get("required", [])}
    if kind == "array":
        return [sample(schema.get("items", {})) for _ in range(schema.get("minItems", 0))]
    if kind == "boolean":
        return True
    if kind in {"integer", "number"}:
        return max(1, schema.get("minimum", 1))
    return "x" * max(1, schema.get("minLength", 1))


def artifact(name):
    return sample(json.loads((ARTIFACT_SCHEMAS_DIR / f"{name}.schema.json").read_text()))


@pytest.fixture
def layout(tmp_path):
    return initialize_project("Build a small web app", projects_dir=tmp_path,
                              name="proposal-review", pipeline_name="web-app-lite")


def test_failure_signature_distinguishes_improved_test_evidence():
    first = {"code": "STAGE_CHECK_FAILED", "failed_checks": [{
        "kind": "tool", "name": "run_tests", "required": True,
        "reason": "Command exited with 1", "log_tail": "10 failed",
        "failed_test_ids": [f"test-{i}" for i in range(10)]}]}
    second = copy.deepcopy(first)
    second["failed_checks"][0].update(log_tail="1 failed", failed_test_ids=["test-0"])
    assert failure_signature(first) != failure_signature(second)


def test_snapshot_paths_and_durations_do_not_make_a_new_failure():
    def failed(identifier, elapsed):
        return {"code": "STAGE_CHECK_FAILED", "failed_checks": [{"name": "run_tests", "evidence":
            f"/tmp/project/.appforge-control/owner/snapshots/{identifier}/test.js failed after {elapsed}ms"}]}
    assert failure_signature(failed("a" * 32, 100)) == failure_signature(failed("b" * 32, 200))


@pytest.mark.parametrize("change_source", [False, True])
def test_tool_repetition_tracks_source_progress(layout, monkeypatch, change_source):
    (layout.root / "app.txt").write_text("initial")
    responses = []
    def events(*args, **kwargs):
        for index in range(3):
            if change_source:
                (layout.root / "app.txt").write_text(f"repair {index}")
            yield {"type": "tool_call", "call_id": str(index), "name": "read_text", "arguments": {"path": "app.txt"}}
        yield {"type": "cancelled"}
    monkeypatch.setattr(llm_bridge, "agent_start", lambda *a, **k: {"session_id": "repeat-test"})
    monkeypatch.setattr(llm_bridge, "agent_events", events)
    monkeypatch.setattr(llm_bridge, "agent_tool_result", lambda *a, **k: responses.append(json.loads(k["result"])))
    monkeypatch.setattr(llm_bridge, "agent_stop", lambda *a, **k: None)
    LLMBridgeAgentDriver(bridge_url="http://bridge.test").run("fixture", layout=layout, stage="implementation", attempt=1, timeout=10)
    assert len(responses) == 3
    assert (responses[-1]["data"].get("code") == "REPEATED_IDENTICAL_TOOL_CALL") is (not change_source)


def test_three_attempt_budget_allows_one_strategy_change(layout):
    class Driver(AgentDriver):
        name = "review-failure-fixture"
        calls = 0

        def run(self, *args, **kwargs):
            self.calls += 1
            raise DriverError("review fixture failure")

    driver = Driver()
    result = PipelineRunner(layout, driver, max_stage_attempts=3).run(only_stage="intake")
    assert not result.success
    assert driver.calls == 3
    assert result.failure["retry_decision"] == "stop"


def test_zero_exit_without_test_or_build_evidence_fails_gates(layout, monkeypatch):
    (layout.root / "package.json").write_text(json.dumps({
        "scripts": {"test": "node -e 0", "build": "node -e 0"}}))
    # Controlled command result; this test does not execute npm or bypass a sandbox.
    monkeypatch.setattr(quality, "run_command", lambda *a, **k:
                        ToolResult(True, data={"returncode": 0, "stdout": "", "stderr": ""}))
    stage = StageSpec("quality", "review", "stages/verification.md", (),
                      gates=(GateSpec("run_tests"), GateSpec("run_build")))
    passed, records = run_declared_gates(layout, stage=stage, allow_network=False,
                                        allow_destructive=False)
    assert not passed and not any(record["passed"] for record in records)
    assert not (layout.root / "dist").exists()


def test_static_html_requires_an_accepted_behavioral_contract(layout):
    (layout.root / "index.html").write_text("<!doctype html><title>Review</title>")
    stage = StageSpec("verification", "review", "stages/verification.md", (),
                      gates=(GateSpec("run_tests"), GateSpec("run_build")))
    passed, records = run_declared_gates(layout, stage=stage, allow_network=False,
                                        allow_destructive=False)
    assert not passed
    assert records[0]["result"]["data"]["code"] == "ACCEPTANCE_CONTRACT_INVALID"


def test_false_and_unverified_report_cannot_complete_verification(layout):
    report = artifact("verification_report")
    report.update(passed=False, test_commands=["never-executed-command"],
                  results=[{"check": "never-executed", "passed": "not verified", "evidence": "none"}],
                  acceptance_results=[{"requirement": "UNKNOWN", "passed": "not verified", "evidence": "none"}])
    atomic_write_json(layout.artifacts / "verification_report.json", report)
    passed, _, _ = validate_stage_artifacts(layout, load_pipeline("web-app-lite").stage("verification"))
    assert not passed


def test_duplicate_lite_requirement_ids_fail_artifact_validation(layout):
    spec = artifact("engineering_spec")
    requirements = spec["requirements"]
    requirements.append(copy.deepcopy(requirements[0]))
    atomic_write_json(layout.artifacts / "engineering_spec.json", spec)
    passed, _, _ = validate_stage_artifacts(layout, load_pipeline("web-app-lite").stage("engineering_spec"))
    assert not passed


def test_readiness_rejects_non_json_report(layout):
    (layout.root / "README.md").write_text("Review fixture")
    (layout.root / ".gitignore").write_text("cache/\n")
    (layout.artifacts / "verification_report.json").write_text("not JSON")
    assert not ReleaseReadinessTool().run(layout.root, {}).success


def test_handoff_envelope_can_change_source(layout):
    source = layout.root / "index.html"
    source.write_text("previously checked")
    envelope = {"files": {"index.html": "changed after verification"},
                "artifacts": {"handoff_report": artifact("handoff_report")},
                "stage_result": {"stage": "handoff", "status": "completed", "summary": "handoff"}}
    _apply_bridge_envelope(layout, stage="handoff", produces=("handoff_report",),
                           response_text=json.dumps(envelope))
    assert source.read_text() == "changed after verification"


def test_registered_tool_mutation_is_in_observed_changes(layout, monkeypatch):
    driver = LLMBridgeAgentDriver(bridge_url="http://review.invalid")

    def mutate(*args):
        (layout.root / "changed.txt").write_text("registered tool output")
        return ToolResult(True)

    monkeypatch.setattr(driver, "_execute_registered_tool", mutate)
    _, changed, _, is_error = driver._execute_agent_tool(
        layout, stage="implementation", name="run_command", arguments={},
        project=load_project(layout), tool_log_path=layout.logs / "review-tools.jsonl")
    assert not is_error and (layout.root / "changed.txt").is_file()
    assert changed == {"changed.txt"}


def test_submission_rejects_missing_required_verification_artifact(layout):
    result = LLMBridgeAgentDriver(bridge_url="http://review.invalid")._submit_stage_result(
        layout, "verification", {"payload": json.dumps({
            "stage": "verification", "status": "completed", "summary": "self report"})},
        observed_changes=set())
    assert not result.success
    assert not (layout.artifacts / "verification_report.json").exists()


def test_local_import_failure_is_repairable(layout):
    (layout.root / "package.json").write_text("{}")
    (layout.root / "node_modules").mkdir()
    result = ToolResult(False, data={"returncode": 1, "stderr":
        "Error [ERR_MODULE_NOT_FOUND]: Cannot find module './src/missing.js'"})
    assert quality._toolchain_missing_reason(layout.root, result) is None


def test_concerns_with_critical_blocks_stage_completion(layout, monkeypatch):
    monkeypatch.setattr("appforge.runner.run_declared_gates", lambda *a, **k: (True, []))
    pipeline = load_pipeline("web-app-lite")
    for stage in ("intake", "engineering_spec"):
        write_checkpoint(layout, pipeline=pipeline, stage=stage, status="completed", attempt=1)

    class Driver(AgentDriver):
        name = "review-success-fixture"
        bridge_url = "http://review.invalid"

        def run(self, prompt, *, layout, stage, **kwargs):
            _apply_bridge_envelope(layout, stage=stage, produces=("implementation_report",),
                response_text=json.dumps({
                    "files": {"index.html": "<title>Review fixture</title>"},
                    "artifacts": {"implementation_report": artifact("implementation_report")},
                    "stage_result": {"stage": stage, "status": "completed", "summary": "fixture"}}))
            return DriverResult(True, 0, "", "", 0, ["review-fixture"])

    monkeypatch.setattr(llm_bridge, "generate", lambda *a, **k: {"text": json.dumps({
        "verdict": "concerns", "findings": [{"severity": "critical", "finding": "review fixture critical",
                                                "proposed_fix": "block completion"}]})})
    result = PipelineRunner(layout, Driver(), max_stage_attempts=1).run(only_stage="implementation")
    checkpoint = read_checkpoint(layout, "implementation")
    assert not result.success and checkpoint["status"] == "failed"
    assert checkpoint["review"]["critical_count"] == 1
    assert any(item["finding"] == "review fixture critical" for item in checkpoint["review"]["findings"])


def test_resume_rejects_mismatched_checkpoint_version(layout):
    pipeline = load_pipeline("web-app-lite")
    for stage in pipeline.stages:
        path = write_checkpoint(layout, pipeline=pipeline, stage=stage.name, status="completed", attempt=1)
        checkpoint = json.loads(path.read_text())
        checkpoint["pipeline_version"] = "obsolete-policy-version"
        atomic_write_json(path, checkpoint)
    (layout.root / "index.html").write_text("changed after checkpoints")
    assert next_stage(layout, pipeline) == pipeline.stages[0].name
