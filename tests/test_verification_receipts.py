from __future__ import annotations

import hashlib
import json
import zipfile
import shutil
import os
from pathlib import Path

import pytest

from appforge import validation_service
from appforge.browser_checks import contract_errors
from appforge.checkpoints import next_stage, write_checkpoint
from appforge.evidence import begin_attempt, execution_context, read_host
from appforge.models import ToolResult
from appforge.pipelines import load_pipeline
from appforge.projects import initialize_project, load_project
from appforge.tooling.tools.release import ArchiveWorkspaceTool
from appforge.util import atomic_write_json
from appforge.validation_service import archive_verification, current_receipt, validate_candidate


def report_for(receipt):
    browser_id = next(record["execution_id"] for record in receipt["records"] if record["name"] == "browser_acceptance")
    return {"schema_version": "2.0", "summary": "Measured counter verification", "passed": True,
            "execution_ids": receipt["execution_ids"], "test_commands": [record["name"] for record in receipt["records"]],
            "results": [{"check": record["name"], "passed": True, "evidence": "Host execution",
                         "evidence_ids": [record.get("execution_id") or record["result"]["data"]["execution_id"]]} for record in receipt["records"]],
            "acceptance_results": [{"requirement": case["criterion_id"].split("/")[0], "criterion_id": case["criterion_id"],
                                    "passed": True, "evidence": "Observed browser action and assertion", "evidence_ids": [browser_id]}
                                   for case in receipt["acceptance_results"]],
            "coverage": "Accepted browser criteria only", "failures": [], "manual_checks": []}


@pytest.fixture
def static_app(tmp_path):
    layout = initialize_project("Build a counter", projects_dir=tmp_path, name="counter", pipeline_name="web-app-lite")
    (layout.root / "index.html").write_text('<button id="add" onclick="count.textContent=Number(count.textContent)+1">Add</button><output id="count">0</output>')
    (layout.root / "README.md").write_text("Open index.html")
    spec = {"schema_version": "1.0", "summary": "Counter", "requirements": [
        {"id": "FR-001", "description": "Increment", "acceptance": ["Click Add to increment"]}],
        "verification_profile": "static-web", "screens": [], "state_and_data": [],
        "implementation_plan": ["Implement counter"], "verification_plan": ["Use browser contract"], "risks": [],
        "browser_checks": [{"criterion_id": "FR-001/AC-01", "actions": [{"kind": "click", "selector": "#add"}],
                             "assertions": [{"kind": "text", "selector": "#count", "expected": "1"}]}]}
    atomic_write_json(layout.artifacts / "engineering_spec.json", spec)
    pipeline = load_pipeline("web-app-lite")
    write_checkpoint(layout, pipeline=pipeline, stage="intake", status="completed", attempt=1)
    write_checkpoint(layout, pipeline=pipeline, stage="engineering_spec", status="completed", attempt=1)
    return layout


@pytest.fixture
def observed_browser(monkeypatch):
    calls = []

    def run(root, checks, **kwargs):
        calls.append(root)
        assert (root / "index.html").is_file()
        return ToolResult(True, data={"acceptance_results": [
            {"criterion_id": case["criterion_id"], "status": "passed"} for case in checks]})

    monkeypatch.setattr(validation_service, "run_browser_checks", run)
    return calls


def test_static_profile_validates_without_package_test_or_build(static_app, observed_browser):
    receipt = validate_candidate(static_app)
    assert receipt["passed"], receipt
    assert {record["name"] for record in receipt["records"]} == {"secret_scan", "browser_acceptance"}
    assert current_receipt(static_app)["receipt_id"] == receipt["receipt_id"]
    assert observed_browser[0] != static_app.root
    for identifier in receipt["execution_ids"]:
        record = read_host(static_app.root, identifier + ".json")
        assert record["input_snapshot_id"] == receipt["input_snapshot_id"]


def test_failed_browser_flow_cannot_create_archive(static_app, monkeypatch):
    monkeypatch.setattr(validation_service, "run_browser_checks", lambda *a, **k: ToolResult(False, error="Counter did not change"))
    result = ArchiveWorkspaceTool().run(static_app.root, {})
    assert not result.success
    assert result.data["code"] == "FINAL_VALIDATION_FAILED"
    assert not list(static_app.reports.glob("*.zip"))


def test_archive_contains_verified_bytes_and_detects_replacement(static_app, observed_browser):
    result = ArchiveWorkspaceTool().run(static_app.root, {})
    assert result.success, result.error
    archive = static_app.root / result.data["archive"]
    assert archive_verification(static_app, archive)["status"] == "verified"
    with zipfile.ZipFile(archive) as handle:
        for name, entry in result.data["members"].items():
            assert hashlib.sha256(handle.read(name)).hexdigest() == entry["sha256"]
    archive.write_bytes(b"replaced archive")
    assert archive_verification(static_app, archive)["status"] == "stale"


def test_source_change_invalidates_receipt_and_download(static_app, observed_browser):
    result = ArchiveWorkspaceTool().run(static_app.root, {})
    assert result.success
    (static_app.root / "index.html").write_text("broken")
    assert current_receipt(static_app) is None
    assert archive_verification(static_app, static_app.root / result.data["archive"])["status"] == "stale"


def test_changed_accepted_spec_is_rejected(static_app, observed_browser):
    spec = json.loads((static_app.artifacts / "engineering_spec.json").read_text())
    spec["browser_checks"][0]["assertions"][0]["expected"] = "0"
    atomic_write_json(static_app.artifacts / "engineering_spec.json", spec)
    receipt = validate_candidate(static_app)
    assert not receipt["passed"] and receipt["code"] == "SPEC_NOT_ACCEPTED"
    assert not observed_browser


def test_only_identical_inputs_in_same_epoch_reuse_evidence(static_app, observed_browser):
    with execution_context(epoch="test-attempt"):
        first = validate_candidate(static_app)
        assert validate_candidate(static_app)["receipt_id"] == first["receipt_id"]
        (static_app.root / "package-lock.json").write_text("{}")
        assert validate_candidate(static_app)["receipt_id"] != first["receipt_id"]
        before_retry = validate_candidate(static_app)["receipt_id"]
        begin_attempt(2)
        assert validate_candidate(static_app)["receipt_id"] != before_retry
    assert len(observed_browser) == 3
    validate_candidate(static_app)
    assert len(observed_browser) == 4


def test_workspace_metadata_cannot_change_governing_pipeline(static_app):
    atomic_write_json(static_app.control / "project.json", {"pipeline": "prototype", "safety": {"allow_network": True}})
    assert load_project(static_app)["pipeline"] == "web-app-lite"
    assert load_project(static_app)["safety"]["allow_network"] is False


def test_modified_completed_source_resumes_verification(static_app):
    pipeline = load_pipeline("web-app-lite")
    for stage in pipeline.stages[2:]:
        write_checkpoint(static_app, pipeline=pipeline, stage=stage.name, status="completed", attempt=1)
    (static_app.root / "index.html").write_text("changed after handoff")
    assert next_stage(static_app, pipeline) == "verification"


def test_unknown_and_duplicate_criteria_are_rejected(static_app):
    spec = json.loads((static_app.artifacts / "engineering_spec.json").read_text())
    assert not contract_errors(spec)
    spec["browser_checks"].append(spec["browser_checks"][0])
    assert contract_errors(spec)
    spec["browser_checks"] = [{**spec["browser_checks"][0], "criterion_id": "UNKNOWN/AC-01"}]
    assert contract_errors(spec)


def test_rerunning_verification_invalidates_later_completions(static_app, observed_browser):
    pipeline = load_pipeline("web-app-lite")
    for stage in pipeline.stages[2:]:
        write_checkpoint(static_app, pipeline=pipeline, stage=stage.name, status="completed", attempt=1)
    (static_app.root / "README.md").write_text("Updated installation instructions")
    assert next_stage(static_app, pipeline) == "verification"
    write_checkpoint(static_app, pipeline=pipeline, stage="verification", status="in_progress", attempt=2)
    validate_candidate(static_app)
    write_checkpoint(static_app, pipeline=pipeline, stage="verification", status="completed", attempt=2)
    assert next_stage(static_app, pipeline) == "handoff"


def test_web_report_requires_matching_commands_criteria_and_current_execution_ids(static_app, observed_browser):
    from appforge.artifacts import validate_artifact
    from appforge.gates import verification_report_errors
    receipt = validate_candidate(static_app)
    report = report_for(receipt)
    validate_artifact("verification_report", report)
    assert not verification_report_errors(static_app, report)
    report["test_commands"].append("npm run fictional-test")
    assert any("Command has no" in error for error in verification_report_errors(static_app, report))
    report = report_for(receipt)
    report["acceptance_results"][0]["evidence_ids"] = [receipt["execution_ids"][0]]
    assert any("does not observe" in error for error in verification_report_errors(static_app, report))
    report = report_for(receipt)
    report["passed"] = False
    report["acceptance_results"][0]["passed"] = False
    validate_artifact("verification_report", report)  # Failure remains a saveable artifact.
    assert verification_report_errors(static_app, report)
    report = report_for(receipt)
    (static_app.root / "README.md").write_text("Changed after the original verification")
    validate_candidate(static_app, reuse=False)
    assert verification_report_errors(static_app, report)


def test_missing_browser_is_blocked_not_passed(static_app, monkeypatch):
    monkeypatch.setattr(validation_service, "run_browser_checks", lambda *a, **k: ToolResult(False,
        error="Browser runtime absent", data={"code": "BROWSER_UNAVAILABLE", "status": "blocked"}))
    result = validate_candidate(static_app)
    assert result["status"] == "blocked" and not result["passed"]


def test_download_keeps_verified_bytes_when_archive_path_is_replaced(static_app, observed_browser, tmp_path):
    from appforge.web_jobs import JobManager, WebConfig, WebJobError
    archived = ArchiveWorkspaceTool().run(static_app.root, {})
    archive = static_app.root / archived.data["archive"]
    manager = JobManager(WebConfig(projects_dir=tmp_path, data_dir=tmp_path / "state"))
    manager._jobs["test"] = {"id": "test", "status": "completed", "project_path": str(static_app.root),
                             "archive_path": str(archive), "download": {"filename": archive.name}}
    stream, _ = manager.download_stream("test")
    archive.write_bytes(b"replaced")
    before = stream.read()
    stream.close()
    assert hashlib.sha256(before).hexdigest() == archived.data["archive_sha256"]
    with pytest.raises(WebJobError, match="검증 증거"):
        manager.download_path("test")
    archive.with_suffix(".zip.manifest.json").write_text('{"verification_status":"verified"}')
    assert manager._archive_verification(manager._jobs["test"])["status"] == "stale"


@pytest.fixture
def browser_runtime():
    pytest.importorskip("playwright.sync_api")
    if not os.environ.get("APPFORGE_RUN_BROWSER_TESTS"):
        pytest.skip("Set APPFORGE_RUN_BROWSER_TESTS=1 after installing Playwright Chromium")


def test_real_browser_counter_and_broken_interaction(static_app, browser_runtime):
    from appforge.browser_checks import run_browser_checks
    checks = json.loads((static_app.artifacts / "engineering_spec.json").read_text())["browser_checks"]
    assert run_browser_checks(static_app.root, checks, timeout=15).success
    (static_app.root / "index.html").write_text('<button id="add">Add</button><output id="count">0</output>')
    broken = run_browser_checks(static_app.root, checks, timeout=2)
    assert not broken.success and broken.data["acceptance_results"][0]["status"] == "failed"


@pytest.mark.parametrize("profile", ["javascript-web", "typescript-web"])
def test_real_build_tests_and_browser_verify_same_snapshot(static_app, browser_runtime, profile):
    typescript = Path(__file__).resolve().parents[1] / "frontend" / "node_modules" / "typescript"
    if not shutil.which("node") or not shutil.which("npm") or (profile == "typescript-web" and not typescript.is_dir()):
        pytest.skip("Node/npm and TypeScript compiler required")
    spec = json.loads((static_app.artifacts / "engineering_spec.json").read_text())
    spec["verification_profile"] = profile
    atomic_write_json(static_app.artifacts / "engineering_spec.json", spec)
    write_checkpoint(static_app, pipeline=load_pipeline("web-app-lite"), stage="engineering_spec", status="completed", attempt=2)
    (static_app.root / "test").mkdir()
    (static_app.root / "test" / "counter.test.cjs").write_text("const {test}=require('node:test');const assert=require('node:assert/strict');test('initial counter',()=>assert.match(require('fs').readFileSync('index.html','utf8'),/>0</));")
    (static_app.root / "build.cjs").write_text("const fs=require('fs');fs.mkdirSync('dist',{recursive:true});fs.copyFileSync('index.html','dist/index.html');")
    scripts = {"test": "node --test", "build": "node build.cjs"}
    if profile == "typescript-web":
        shutil.copytree(typescript, static_app.root / "node_modules" / "typescript")
        (static_app.root / "counter.ts").write_text("const counter: number = 0;")
        (static_app.root / "tsconfig.json").write_text('{"compilerOptions":{"noEmit":true},"include":["counter.ts"]}')
        scripts["typecheck"] = "node node_modules/typescript/bin/tsc --noEmit"
    atomic_write_json(static_app.root / "package.json", {"name": "counter", "private": True, "scripts": scripts})
    receipt = validate_candidate(static_app, timeout=40)
    assert receipt["passed"], receipt
    assert receipt["profile"] == profile
    test_record = next(record for record in receipt["records"] if record["name"] == "run_tests")
    assert test_record["result"]["data"]["tests"]["executed"] == 1
    (static_app.root / "build.cjs").write_text("require('fs').writeFileSync('index.html', 'self-modified source')")
    broken = validate_candidate(static_app, timeout=40)
    assert not broken["passed"]
    assert "self-modified source" not in (static_app.root / "index.html").read_text()
