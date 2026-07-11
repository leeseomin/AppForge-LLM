from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from appforge import llm_bridge
from appforge.drivers import (
    AgentDriver,
    DriverError,
    LLMBridgeAgentDriver,
    LLMBridgeDriver,
    _apply_bridge_envelope,
    _bridge_envelope_response_format,
    _build_bridge_prompt,
    create_driver,
)
from appforge.models import DriverResult
from appforge.pipelines import load_pipeline
from appforge.projects import initialize_project
from appforge.runner import PipelineRunner


class NoopSuccessDriver(AgentDriver):
    name = "noop"

    def run(self, prompt, *, layout, stage, attempt, timeout, cancel_event=None):  # type: ignore[no-untyped-def]
        return DriverResult(True, 0, "ok", "", 0.01, ["noop"])


class FailingDriver(AgentDriver):
    name = "failing"

    def run(self, prompt, *, layout, stage, attempt, timeout, cancel_event=None):  # type: ignore[no-untyped-def]
        raise DriverError("fixture driver failure")


class FixtureScriptDriver(AgentDriver):
    name = "fixture"

    def __init__(self, fixture: Path, framework_root: Path) -> None:
        self.fixture = fixture
        self.framework_root = framework_root

    def run(self, prompt, *, layout, stage, attempt, timeout, cancel_event=None):  # type: ignore[no-untyped-def]
        final_path = layout.logs / f"{stage}-attempt-{attempt}-fixture-final.txt"
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(self.fixture),
                    str(layout.root),
                    stage,
                    str(self.framework_root),
                ],
                cwd=layout.root,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            final_path.write_text(stdout, encoding="utf-8")
            return DriverResult(False, 124, stdout, stderr, float(timeout), ["fixture"], str(final_path))
        final_path.write_text(completed.stdout, encoding="utf-8")
        return DriverResult(
            completed.returncode == 0,
            completed.returncode,
            completed.stdout,
            completed.stderr,
            0.01,
            ["fixture"],
            str(final_path),
        )


def test_auto_driver_resolves_to_llm_bridge_agent() -> None:
    driver = create_driver("auto")
    assert isinstance(driver, LLMBridgeAgentDriver)
    assert driver.bridge_url == "http://127.0.0.1:8788"


@pytest.mark.parametrize("name", ["codex", "claude", "generic"])
def test_cli_and_command_drivers_are_removed(name: str) -> None:
    with pytest.raises(DriverError, match="removed|Unknown driver"):
        create_driver(name)


def test_llm_bridge_prompt_includes_stage_artifact_schema(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-prompt",
        pipeline_name="web-app",
    )
    prompt, produces = _build_bridge_prompt("original stage packet", layout=layout, stage_name="intake")
    assert produces == ("product_brief",)
    assert "External LLM bridge execution contract" in prompt
    assert "product_brief" in prompt
    assert "original stage packet" in prompt


def test_llm_bridge_envelope_response_format_matches_bridge_contract() -> None:
    response_format = _bridge_envelope_response_format("intake", ("product_brief",))

    assert response_format["type"] == "json"
    assert "json_schema" not in response_format
    assert response_format["schema"]["required"] == ["artifacts", "stage_result", "files"]
    assert response_format["schema"]["properties"]["artifacts"]["required"] == ["product_brief"]


def test_llm_bridge_driver_retries_transient_generate_failure(tmp_path, monkeypatch) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-transient-retry",
        pipeline_name="web-app",
    )
    calls = 0
    response = json.dumps(
        {
            "artifacts": {"product_brief": _valid_product_brief()},
            "stage_result": _valid_stage_result(),
            "files": {},
        }
    )

    def fake_generate(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise llm_bridge.BridgeError(
                "temporary connection reset",
                status_code=502,
                payload={"error": {"code": "BRIDGE_CONNECTION_ERROR"}},
            )
        return {"text": response}

    monkeypatch.setattr(llm_bridge, "generate", fake_generate)
    monkeypatch.setattr(llm_bridge, "sleep_with_cancel", lambda *args, **kwargs: None)

    result = LLMBridgeDriver(bridge_url="http://bridge.test").run(
        "stage packet",
        layout=layout,
        stage="intake",
        attempt=1,
        timeout=120,
    )

    assert result.success is True
    assert calls == 2
    assert (layout.artifacts / "product_brief.json").is_file()


def test_llm_bridge_driver_maps_cancelled_request_to_cancel_result(tmp_path, monkeypatch) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-cancel",
        pipeline_name="web-app",
    )
    cancel_event = threading.Event()

    def fake_generate(*args, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["cancel_event"] is cancel_event
        raise llm_bridge.BridgeCancelled()

    monkeypatch.setattr(llm_bridge, "generate", fake_generate)
    driver = LLMBridgeDriver(bridge_url="http://bridge.test")

    result = driver.run(
        "stage packet",
        layout=layout,
        stage="intake",
        attempt=1,
        timeout=120,
        cancel_event=cancel_event,
    )

    assert result.success is False
    assert result.exit_code == 130
    assert result.stderr == "Cancelled by user."


def test_llm_bridge_agent_continues_after_transient_stream_interruption(tmp_path, monkeypatch) -> None:
    layout = initialize_project(
        "Build a tiny web game",
        projects_dir=tmp_path,
        name="bridge-agent-stream-recovery",
        pipeline_name="web-app-lite",
    )
    sessions: list[str] = []
    relayed: list[dict] = []

    def fake_agent_start(*args, **kwargs):  # type: ignore[no-untyped-def]
        sessions.append(str(kwargs["prompt"]))
        return {"session_id": f"session-{len(sessions)}"}

    def fake_agent_events(*args, **kwargs):  # type: ignore[no-untyped-def]
        session_id = str(args[1])
        if session_id == "session-1":
            yield {"type": "text_delta", "text": "Starting files..."}
            raise llm_bridge.BridgeError(
                "stream closed before done",
                status_code=502,
                payload={"error": {"code": "BRIDGE_STREAM_INTERRUPTED"}},
            )
        yield {
            "type": "tool_call",
            "call_id": "write-recovery",
            "name": "write_text",
            "arguments": {"path": "index.html", "content": "<!doctype html><title>Recovered</title>"},
        }
        yield {
            "type": "tool_call",
            "call_id": "artifact-recovery",
            "name": "submit_artifact",
            "arguments": {
                "name": "implementation_report",
                "payload": _valid_implementation_report(["index.html"]),
            },
        }
        yield {
            "type": "tool_call",
            "call_id": "result-recovery",
            "name": "submit_stage_result",
            "arguments": {
                "payload": {
                    "stage": "implementation",
                    "status": "completed",
                    "summary": "Recovered after a transient bridge interruption.",
                    "files_changed": ["index.html"],
                    "commands_run": [],
                    "checks": [],
                    "decisions": [],
                    "unresolved": [],
                }
            },
        }
        yield {"type": "done", "usage": {"total_tokens": 10}}

    monkeypatch.setattr(llm_bridge, "agent_start", fake_agent_start)
    monkeypatch.setattr(llm_bridge, "agent_events", fake_agent_events)
    monkeypatch.setattr(llm_bridge, "agent_tool_result", lambda *args, **kwargs: {})
    monkeypatch.setattr(llm_bridge, "agent_stop", lambda *args, **kwargs: {})
    monkeypatch.setattr(llm_bridge, "sleep_with_cancel", lambda *args, **kwargs: None)

    driver = LLMBridgeAgentDriver(bridge_url="http://bridge.test")
    driver.set_event_handler(relayed.append)
    result = driver.run(
        "stage packet",
        layout=layout,
        stage="implementation",
        attempt=1,
        timeout=120,
    )

    assert result.success is True
    assert len(sessions) == 2
    assert any(event.get("type") == "bridge_recovering" for event in relayed)
    assert "transport recovery" in sessions[1]
    assert (layout.root / "index.html").is_file()
    assert (layout.artifacts / "implementation_report.json").is_file()


def test_llm_bridge_agent_accepts_validated_submissions_after_lost_done_event(tmp_path, monkeypatch) -> None:
    layout = initialize_project(
        "Build a tiny web game",
        projects_dir=tmp_path,
        name="bridge-agent-lost-done",
        pipeline_name="web-app-lite",
    )
    sessions = 0

    def fake_agent_start(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal sessions
        sessions += 1
        return {"session_id": f"session-{sessions}"}

    def fake_agent_events(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield {
            "type": "tool_call",
            "call_id": "write-complete",
            "name": "write_text",
            "arguments": {"path": "index.html", "content": "<!doctype html><title>Complete</title>"},
        }
        yield {
            "type": "tool_call",
            "call_id": "artifact-complete",
            "name": "submit_artifact",
            "arguments": {
                "name": "implementation_report",
                "payload": _valid_implementation_report(["index.html"]),
            },
        }
        yield {
            "type": "tool_call",
            "call_id": "result-complete",
            "name": "submit_stage_result",
            "arguments": {
                "payload": {
                    "stage": "implementation",
                    "status": "completed",
                    "summary": "All required submissions were validated.",
                    "files_changed": ["index.html"],
                    "commands_run": [],
                    "checks": [],
                    "decisions": [],
                    "unresolved": [],
                }
            },
        }
        raise llm_bridge.BridgeError(
            "done event was lost",
            status_code=502,
            payload={"error": {"code": "BRIDGE_STREAM_INTERRUPTED"}},
        )

    monkeypatch.setattr(llm_bridge, "agent_start", fake_agent_start)
    monkeypatch.setattr(llm_bridge, "agent_events", fake_agent_events)
    monkeypatch.setattr(llm_bridge, "agent_tool_result", lambda *args, **kwargs: {})
    monkeypatch.setattr(llm_bridge, "agent_stop", lambda *args, **kwargs: {})

    result = LLMBridgeAgentDriver(bridge_url="http://bridge.test").run(
        "stage packet",
        layout=layout,
        stage="implementation",
        attempt=1,
        timeout=120,
    )

    assert result.success is True
    assert sessions == 1
    assert (layout.control / "stage-result.json").is_file()


def test_llm_bridge_agent_recovers_after_text_only_turn(tmp_path, monkeypatch) -> None:
    layout = initialize_project(
        "Build a tiny web game",
        projects_dir=tmp_path,
        name="bridge-agent-text-only",
        pipeline_name="web-app-lite",
    )
    sessions: list[str] = []

    def fake_agent_start(*args, **kwargs):  # type: ignore[no-untyped-def]
        sessions.append(str(kwargs["prompt"]))
        return {"session_id": f"session-{len(sessions)}"}

    def fake_agent_events(*args, **kwargs):  # type: ignore[no-untyped-def]
        session_id = str(args[1])
        if session_id == "session-1":
            yield {"type": "text_delta", "text": "I will create index.html next."}
            yield {"type": "done", "usage": {"total_tokens": 10}}
            return
        yield {
            "type": "tool_call",
            "call_id": "write-1",
            "name": "write_text",
            "arguments": {"path": "index.html", "content": "<!doctype html><title>Game</title>"},
        }
        yield {
            "type": "tool_call",
            "call_id": "artifact-1",
            "name": "submit_artifact",
            "arguments": {
                "name": "implementation_report",
                "payload": {
                    "schema_version": "1.0",
                    "summary": "Implemented a tiny web game.",
                    "features_completed": ["Playable browser surface"],
                    "files_changed": ["index.html"],
                    "commands_run": [],
                    "decisions": ["Use a single static HTML file"],
                    "known_gaps": [],
                    "requirements_covered": ["Create a tiny web game"],
                },
            },
        }
        yield {
            "type": "tool_call",
            "call_id": "result-1",
            "name": "submit_stage_result",
            "arguments": {
                "payload": {
                    "stage": "implementation",
                    "status": "completed",
                    "summary": "Implementation completed.",
                    "files_changed": ["index.html"],
                    "commands_run": [],
                    "checks": [],
                    "decisions": [],
                    "unresolved": [],
                },
            },
        }
        yield {"type": "done", "usage": {"total_tokens": 10}}

    monkeypatch.setattr(llm_bridge, "agent_start", fake_agent_start)
    monkeypatch.setattr(llm_bridge, "agent_events", fake_agent_events)
    monkeypatch.setattr(llm_bridge, "agent_tool_result", lambda *args, **kwargs: {})
    monkeypatch.setattr(llm_bridge, "agent_stop", lambda *args, **kwargs: {})

    result = LLMBridgeAgentDriver(bridge_url="http://bridge.test").run(
        "stage packet",
        layout=layout,
        stage="implementation",
        attempt=1,
        timeout=120,
    )

    assert result.success is True
    assert len(sessions) == 2
    assert "ended without required tool submissions" in sessions[1]
    assert (layout.root / "index.html").is_file()
    assert (layout.artifacts / "implementation_report.json").is_file()
    assert (layout.control / "stage-result.json").is_file()


def test_llm_bridge_agent_falls_back_to_envelope_after_incomplete_sessions(tmp_path, monkeypatch) -> None:
    layout = initialize_project(
        "Build a tiny Three.js flight sim",
        projects_dir=tmp_path,
        name="bridge-agent-envelope-fallback",
        pipeline_name="web-app-lite",
    )
    sessions: list[str] = []

    def fake_agent_start(*args, **kwargs):  # type: ignore[no-untyped-def]
        sessions.append(str(kwargs["prompt"]))
        return {"session_id": f"session-{len(sessions)}"}

    def fake_agent_events(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield {"type": "text_delta", "text": "I will create the project files next."}
        yield {"type": "done", "usage": {"total_tokens": 10}}

    response = json.dumps(
        {
            "artifacts": {
                "implementation_report": _valid_implementation_report(["index.html"])
            },
            "stage_result": {
                "stage": "implementation",
                "status": "completed",
                "summary": "Implemented fallback-generated app.",
                "files_changed": ["index.html"],
                "commands_run": [],
                "checks": [],
                "decisions": [],
                "unresolved": [],
            },
            "files": {
                "index.html": "<!doctype html><title>Flight Sim</title>",
            },
        }
    )

    monkeypatch.setattr(llm_bridge, "agent_start", fake_agent_start)
    monkeypatch.setattr(llm_bridge, "agent_events", fake_agent_events)
    monkeypatch.setattr(llm_bridge, "agent_tool_result", lambda *args, **kwargs: {})
    monkeypatch.setattr(llm_bridge, "agent_stop", lambda *args, **kwargs: {})
    monkeypatch.setattr(llm_bridge, "generate", lambda *args, **kwargs: {"text": response})

    result = LLMBridgeAgentDriver(bridge_url="http://bridge.test").run(
        "stage packet",
        layout=layout,
        stage="implementation",
        attempt=1,
        timeout=120,
    )

    assert result.success is True
    assert len(sessions) == 3
    assert "fallback" in result.stdout
    assert (layout.root / "index.html").is_file()
    assert (layout.artifacts / "implementation_report.json").is_file()
    assert (layout.control / "stage-result.json").is_file()


def test_llm_bridge_envelope_writes_artifacts_stage_result_and_files(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-envelope",
        pipeline_name="web-app",
    )
    response = json.dumps(
        {
            "artifacts": {"product_brief": _valid_product_brief()},
            "stage_result": {
                "stage": "wrong-stage",
                "status": "completed",
                "summary": "Bridge wrote the intake artifact",
                "files_changed": [],
                "commands_run": [],
                "checks": [{"name": "fixture", "passed": True}],
                "decisions": [],
                "unresolved": [],
            },
            "files": {
                "README.md": "# Bridge fixture\n",
                ".appforge/artifacts/product_brief.json": "{}",
                ".appforge/stage-result.json": "{}",
            },
        }
    )
    changed = _apply_bridge_envelope(
        layout,
        stage="intake",
        produces=("product_brief",),
        response_text=response,
    )
    assert "README.md" in changed
    assert ".appforge/artifacts/product_brief.json" in changed
    assert (layout.root / "README.md").read_text(encoding="utf-8") == "# Bridge fixture\n"
    artifact = json.loads((layout.artifacts / "product_brief.json").read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "1.0"
    stage_result = json.loads((layout.control / "stage-result.json").read_text(encoding="utf-8"))
    assert stage_result["stage"] == "intake"
    assert stage_result["status"] == "completed"
    assert "README.md" in stage_result["files_changed"]


def test_llm_bridge_envelope_rejects_too_many_files(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-file-count-limit",
        pipeline_name="web-app",
    )
    response = json.dumps(
        {
            "artifacts": {"product_brief": _valid_product_brief()},
            "stage_result": _valid_stage_result(),
            "files": {f"src/file-{index}.txt": "ok\n" for index in range(121)},
        }
    )

    with pytest.raises(DriverError, match="Too many files"):
        _apply_bridge_envelope(
            layout,
            stage="intake",
            produces=("product_brief",),
            response_text=response,
        )


def test_llm_bridge_envelope_rejects_oversized_file(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-file-size-limit",
        pipeline_name="web-app",
    )
    response = json.dumps(
        {
            "artifacts": {"product_brief": _valid_product_brief()},
            "stage_result": _valid_stage_result(),
            "files": {"src/big.txt": "x" * 512_001},
        }
    )

    with pytest.raises(DriverError, match="File too large"):
        _apply_bridge_envelope(
            layout,
            stage="intake",
            produces=("product_brief",),
            response_text=response,
        )


def test_llm_bridge_envelope_rejects_oversized_total_payload(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-total-size-limit",
        pipeline_name="web-app",
    )
    response = json.dumps(
        {
            "artifacts": {"product_brief": _valid_product_brief()},
            "stage_result": _valid_stage_result(),
            "files": {f"src/chunk-{index}.txt": "x" * 500_000 for index in range(11)},
        }
    )

    with pytest.raises(DriverError, match="payload is too large"):
        _apply_bridge_envelope(
            layout,
            stage="intake",
            produces=("product_brief",),
            response_text=response,
        )


def test_llm_bridge_envelope_rejects_unmanaged_appforge_file_paths(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-managed-path",
        pipeline_name="web-app",
    )
    response = json.dumps(
        {
            "artifacts": {"product_brief": _valid_product_brief()},
            "files": {".appforge/current-stage.md": "nope"},
        }
    )
    with pytest.raises(DriverError, match="managed path"):
        _apply_bridge_envelope(
            layout,
            stage="intake",
            produces=("product_brief",),
            response_text=response,
        )


def test_llm_bridge_envelope_rejects_unsafe_file_paths(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-unsafe",
        pipeline_name="web-app",
    )
    response = json.dumps(
        {
            "artifacts": {"product_brief": _valid_product_brief()},
            "files": {"../escape.txt": "nope"},
        }
    )
    with pytest.raises(DriverError, match="Unsafe file path"):
        _apply_bridge_envelope(
            layout,
            stage="intake",
            produces=("product_brief",),
            response_text=response,
        )


def test_llm_bridge_envelope_unwraps_wrapper_key(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-unwrap",
        pipeline_name="web-app",
    )
    # Models sometimes echo the contract shape and nest the real envelope under
    # a wrapper key. The extractor must descend into it instead of treating the
    # wrapper as the artifact (which previously crashed the job thread).
    response = json.dumps(
        {
            "required_response_shape": {
                "artifacts": {"product_brief": _valid_product_brief()},
                "stage_result": _valid_stage_result(),
                "files": {},
            }
        }
    )
    changed = _apply_bridge_envelope(
        layout,
        stage="intake",
        produces=("product_brief",),
        response_text=response,
    )
    assert ".appforge/artifacts/product_brief.json" in changed
    artifact = json.loads((layout.artifacts / "product_brief.json").read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "1.0"


def test_llm_bridge_envelope_accepts_bare_artifact(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-bare",
        pipeline_name="web-app",
    )
    # A model may return the artifact object directly without any envelope.
    response = json.dumps(_valid_product_brief())
    changed = _apply_bridge_envelope(
        layout,
        stage="intake",
        produces=("product_brief",),
        response_text=response,
    )
    assert ".appforge/artifacts/product_brief.json" in changed


def test_llm_bridge_envelope_surfaces_validation_error_as_driver_error(tmp_path) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-validation",
        pipeline_name="web-app",
    )
    invalid_brief = _valid_product_brief()
    del invalid_brief["schema_version"]
    response = json.dumps({"artifacts": {"product_brief": invalid_brief}})
    # Schema failures must become a retryable DriverError, never an uncaught
    # ArtifactValidationError that kills the job thread with UNEXPECTED_ERROR.
    with pytest.raises(DriverError, match="schema validation"):
        _apply_bridge_envelope(
            layout,
            stage="intake",
            produces=("product_brief",),
            response_text=response,
        )


def test_llm_bridge_driver_applies_generate_envelope(tmp_path, monkeypatch) -> None:
    layout = initialize_project(
        "Build a small web app",
        projects_dir=tmp_path,
        name="bridge-driver",
        pipeline_name="web-app",
    )
    response = json.dumps(
        {
            "artifacts": {"product_brief": _valid_product_brief()},
            "stage_result": _valid_stage_result(),
        }
    )
    monkeypatch.setattr(llm_bridge, "generate", lambda *args, **kwargs: {"text": response})
    driver = LLMBridgeDriver(bridge_url="http://bridge.test")
    result = driver.run(
        "stage packet",
        layout=layout,
        stage="intake",
        attempt=1,
        timeout=60,
    )
    assert result.success
    assert result.command == ["llm-bridge", "/generate"]
    assert (layout.artifacts / "product_brief.json").is_file()
    assert (layout.control / "stage-result.json").is_file()


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


def _valid_implementation_report(files_changed: list[str]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "summary": "Implemented a browser app.",
        "features_completed": ["Playable browser surface"],
        "files_changed": files_changed,
        "commands_run": [],
        "decisions": ["Use a minimal static app for reliable fallback generation"],
        "known_gaps": [],
        "requirements_covered": ["Build a tiny Three.js flight sim"],
    }


def test_full_prototype_pipeline_completes_with_fixture_agent(tmp_path) -> None:
    framework_root = Path(__file__).resolve().parents[1]
    fixture = framework_root / "tests" / "fixtures" / "fake_stage_agent.py"
    layout = initialize_project(
        "Build a quick MVP prototype",
        projects_dir=tmp_path,
        name="prototype-run",
        pipeline_name="prototype",
        mode="autonomous",
    )
    runner = PipelineRunner(
        layout,
        FixtureScriptDriver(fixture, framework_root),
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
    events: list[dict[str, object]] = []
    summary = PipelineRunner(
        layout,
        FailingDriver(),
        max_stage_attempts=1,
        stage_timeout=60,
        event_handler=events.append,
    ).run(only_stage="intake")
    assert not summary.success
    assert summary.failure is not None
    assert summary.failure["code"] == "DRIVER_ERROR"
    assert summary.failure["stage"] == "intake"
    assert any(event["event"] == "stage_failed" for event in events)
    assert any(event["event"] == "pipeline_failed" for event in events)
    checkpoint = json.loads((layout.checkpoints / "checkpoint_intake.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "failed"
    assert "fixture driver failure" in checkpoint["metadata"]["driver_error"]


def test_repeated_failure_signature_stops_retrying(tmp_path) -> None:
    layout = initialize_project(
        "Build a web app",
        projects_dir=tmp_path,
        name="loop-guard",
        pipeline_name="web-app",
    )
    events: list[dict[str, object]] = []
    runner = PipelineRunner(
        layout,
        FailingDriver(),
        max_stage_attempts=2,
        stage_timeout=60,
        event_handler=events.append,
    )
    stage = load_pipeline("web-app").stage("intake")
    seen: set[str] = set()
    first_failure = {
        "code": "STAGE_CHECK_FAILED",
        "message": "same failure",
        "action": "fix it",
        "stage": stage.name,
        "attempt": 1,
        "failed_checks": [{"name": "tests", "reason": "boom"}],
    }
    second_failure = {
        **first_failure,
        "attempt": 2,
    }

    assert runner._register_failure_signature(  # noqa: SLF001
        stage=stage,
        attempt=1,
        seen=seen,
        failure=first_failure,
    ) is False
    assert runner._register_failure_signature(  # noqa: SLF001
        stage=stage,
        attempt=2,
        seen=seen,
        failure=second_failure,
    ) is True
    assert second_failure["code"] == "REPEATED_FAILURE_LOOP"
    assert second_failure["next_retry_mode"] == "regenerate"
    assert any(event["event"] == "loop_guard_triggered" for event in events)
