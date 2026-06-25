from __future__ import annotations

import json
import sys
from pathlib import Path

from appforge.drivers import AgentDriver, DriverError, GenericCommandDriver
from appforge.models import DriverResult
from appforge.pipelines import load_pipeline
from appforge.projects import initialize_project
from appforge.runner import PipelineRunner


class NoopSuccessDriver(AgentDriver):
    name = "noop"

    def run(self, prompt, *, layout, stage, attempt, timeout):  # type: ignore[no-untyped-def]
        return DriverResult(True, 0, "ok", "", 0.01, ["noop"])


class FailingDriver(AgentDriver):
    name = "failing"

    def run(self, prompt, *, layout, stage, attempt, timeout):  # type: ignore[no-untyped-def]
        raise DriverError("fixture driver failure")


def _valid_product_brief() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "summary": "Fixture",
        "problem": "Verify stale completion handling",
        "target_users": ["tester"],
        "desired_outcomes": ["truthful completion"],
        "constraints": [],
        "assumptions": [],
        "non_goals": [],
        "open_questions": [],
    }


def _valid_stage_result() -> dict[str, object]:
    return {
        "stage": "intake",
        "status": "completed",
        "summary": "old result",
        "files_changed": [],
        "commands_run": [],
        "checks": [],
        "decisions": [],
        "unresolved": [],
    }


def test_full_prototype_pipeline_completes_with_generic_fixture_agent(tmp_path) -> None:
    framework_root = Path(__file__).resolve().parents[1]
    fixture = framework_root / "tests" / "fixtures" / "fake_stage_agent.py"
    layout = initialize_project(
        "Build a quick MVP prototype",
        projects_dir=tmp_path,
        name="prototype-run",
        pipeline_name="prototype",
        mode="autonomous",
    )
    command = f'{sys.executable} -S "{fixture}" {{workspace}} {{stage}} "{framework_root}"'
    runner = PipelineRunner(
        layout,
        GenericCommandDriver(command),
        auto_approve=True,
        max_stage_attempts=1,
        stage_timeout=120,
    )
    summary = runner.run()
    assert summary.success, summary
    assert summary.status == "completed"
    assert len(summary.completed_stages) == len(load_pipeline("prototype").stages)
    assert (layout.control / "COMPLETED.md").is_file()
    archives = list(layout.reports.glob("*-source.zip"))
    assert archives


def test_runner_rejects_stale_completion_record(tmp_path) -> None:
    layout = initialize_project(
        "Build a web app",
        projects_dir=tmp_path,
        name="stale",
        pipeline_name="web-app",
    )
    (layout.artifacts / "product_brief.json").write_text(
        json.dumps(_valid_product_brief()), encoding="utf-8"
    )
    (layout.control / "stage-result.json").write_text(
        json.dumps(_valid_stage_result()), encoding="utf-8"
    )
    summary = PipelineRunner(
        layout,
        NoopSuccessDriver(),
        max_stage_attempts=1,
        stage_timeout=60,
    ).run(only_stage="intake")
    assert not summary.success
    checkpoint = json.loads((layout.checkpoints / "checkpoint_intake.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "failed"
    assert not (layout.control / "stage-result.json").exists()


def test_driver_error_creates_failed_checkpoint(tmp_path) -> None:
    layout = initialize_project(
        "Build a web app",
        projects_dir=tmp_path,
        name="driver-failure",
        pipeline_name="web-app",
    )
    summary = PipelineRunner(
        layout,
        FailingDriver(),
        max_stage_attempts=1,
        stage_timeout=60,
    ).run(only_stage="intake")
    assert not summary.success
    checkpoint = json.loads((layout.checkpoints / "checkpoint_intake.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "failed"
    assert "fixture driver failure" in checkpoint["metadata"]["driver_error"]
