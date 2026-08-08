"""Budget exhaustion must not throw away work the agent already finished.

A stage attempt that submitted every required artifact and a schema-valid stage
result is complete. If the driver reports failure merely because the turn or token
budget ran out on the way to the exit, the runner discards a passing stage, retries
with the same inputs, and the loop guard eventually fails the whole job.
"""

from __future__ import annotations

import json

import pytest

from appforge import llm_bridge
from appforge.drivers import LLMBridgeAgentDriver
from appforge.projects import initialize_project

IMPLEMENTATION_REPORT = {
    "schema_version": "1.0",
    "summary": "Implemented the requested application.",
    "features_completed": ["Upload and convert an image"],
    "files_changed": ["src/App.jsx"],
    "commands_run": ["npm test"],
    "decisions": ["Used plain JavaScript"],
    "known_gaps": [],
    "requirements_covered": ["REQ-001"],
}

STAGE_RESULT = {
    "stage": "implementation",
    "status": "completed",
    "summary": "Implemented the requested application.",
    "files_changed": ["src/App.jsx"],
    "commands_run": [{"command": "npm test", "result": "passed"}],
    "checks": [{"name": "run_tests", "passed": True, "evidence": "2 passed"}],
    "decisions": [{"decision": "Used plain JavaScript", "reason": "Matches the spec"}],
    "unresolved": [],
}


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {"type": "tool_call", "call_id": call_id, "name": name, "arguments": arguments}


@pytest.fixture()
def layout(tmp_path):
    return initialize_project(
        "Build a small web app",
        projects_dir=tmp_path / "projects",
        name="budget-recovery",
        pipeline_name="web-app-lite",
    )


def _install_fake_bridge(monkeypatch, events: list[dict]) -> None:
    monkeypatch.setattr(llm_bridge, "agent_start", lambda *a, **k: {"session_id": "s1"})
    monkeypatch.setattr(llm_bridge, "agent_events", lambda *a, **k: iter(events))
    monkeypatch.setattr(llm_bridge, "agent_tool_result", lambda *a, **k: None)
    monkeypatch.setattr(llm_bridge, "agent_stop", lambda *a, **k: None)


def test_token_budget_after_full_submission_keeps_the_completed_stage(layout, monkeypatch) -> None:
    driver = LLMBridgeAgentDriver(bridge_url="http://bridge.test", max_usage_tokens=1)
    _install_fake_bridge(
        monkeypatch,
        [
            _tool_call(
                "1",
                "submit_artifact",
                {"name": "implementation_report", "payload": json.dumps(IMPLEMENTATION_REPORT)},
            ),
            _tool_call("2", "submit_stage_result", {"payload": json.dumps(STAGE_RESULT)}),
            {"type": "done", "usage": {"total_tokens": 5_000_000}},
        ],
    )

    result = driver.run(
        "stage prompt",
        layout=layout,
        stage="implementation",
        attempt=1,
        timeout=60,
    )

    assert result.success, result.stderr
    assert (layout.artifacts / "implementation_report.json").exists()
    assert (layout.control / "stage-result.json").exists()
    transcript = (layout.logs / "implementation-attempt-1-llm-bridge-agent-final.txt").read_text()
    assert "AGENT_TOKEN_BUDGET_EXCEEDED" in transcript, "token budget never tripped"


def test_token_budget_before_submission_still_fails_with_a_named_cause(layout, monkeypatch) -> None:
    driver = LLMBridgeAgentDriver(bridge_url="http://bridge.test", max_usage_tokens=1)
    _install_fake_bridge(monkeypatch, [{"type": "done", "usage": {"total_tokens": 5_000_000}}])

    result = driver.run(
        "stage prompt",
        layout=layout,
        stage="implementation",
        attempt=1,
        timeout=60,
    )

    assert not result.success
    assert "AGENT_TOKEN_BUDGET_EXCEEDED" in result.stderr
    assert "implementation_report" in result.stderr


def test_turn_budget_after_full_submission_keeps_the_completed_stage(layout, monkeypatch) -> None:
    driver = LLMBridgeAgentDriver(bridge_url="http://bridge.test", max_turns=1)
    scaled_ceiling = int(driver.max_turns * driver.STAGE_BUDGET_MULTIPLIER["implementation"])
    overshoot = [
        _tool_call(str(index + 3), "git_status", {}) for index in range(scaled_ceiling + 1)
    ]
    _install_fake_bridge(
        monkeypatch,
        [
            _tool_call(
                "1",
                "submit_artifact",
                {"name": "implementation_report", "payload": json.dumps(IMPLEMENTATION_REPORT)},
            ),
            _tool_call("2", "submit_stage_result", {"payload": json.dumps(STAGE_RESULT)}),
            *overshoot,
            {"type": "done", "usage": {"total_tokens": 10}},
        ],
    )

    result = driver.run(
        "stage prompt",
        layout=layout,
        stage="implementation",
        attempt=1,
        timeout=60,
    )

    assert result.success, result.stderr
    transcript = (layout.logs / "implementation-attempt-1-llm-bridge-agent-final.txt").read_text()
    assert "AGENT_TURN_BUDGET_EXCEEDED" in transcript, "turn budget never tripped"
