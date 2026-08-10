from __future__ import annotations

import io
import json
import queue
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from appforge import llm_bridge
from appforge.drivers import AgentDriver
from appforge.models import DriverResult, ToolResult
from appforge.projects import initialize_project
from appforge.web import create_app
from appforge.web_jobs import JobManager, WebConfig, WebJobError


SESSION_TOKEN = "test-token"


class _FakeBridgeManager:
    def __init__(self, error: llm_bridge.BridgeError | None = None) -> None:
        self.error = error
        self.ensure_calls: list[tuple[str, str | None]] = []
        self.shutdown_calls = 0

    def ensure_running(
        self,
        base_url: str,
        initial_error: llm_bridge.BridgeError | None = None,
    ) -> None:
        self.ensure_calls.append((base_url, str(initial_error) if initial_error else None))
        if self.error:
            raise self.error

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _fixture_config(tmp_path: Path) -> WebConfig:
    return WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        driver="llm-bridge",
        llm_bridge_url="http://bridge.test",
        allow_network=False,
        max_stage_attempts=1,
        stage_timeout=120,
    )


def _client(app, *, raise_server_exceptions: bool = True) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-AppForge-Token": SESSION_TOKEN},
        raise_server_exceptions=raise_server_exceptions,
    )


def _minimal_job(job_id: str, *, status: str = "running") -> dict:
    now = "2026-07-05T00:00:00Z"
    return {
        "id": job_id,
        "version": "2.0",
        "prompt": "테스트 앱",
        "status": status,
        "message": "테스트 중",
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "completed_at": now if status in {"completed", "failed", "cancelled"} else None,
        "pipeline": None,
        "pipeline_description": None,
        "driver": None,
        "project_name": None,
        "project_path": None,
        "active_stage": None,
        "stages": [],
        "events": [],
        "error": None,
        "archive_path": None,
        "download": {
            "available": False,
            "url": None,
            "filename": None,
            "size_bytes": None,
        },
        "preview": {
            "available": False,
            "url": None,
            "path": None,
            "built_at": None,
        },
    }


def test_web_config_defaults_disable_network(monkeypatch) -> None:
    monkeypatch.delenv("APPFORGE_ALLOW_NETWORK", raising=False)

    assert WebConfig().allow_network is False
    assert WebConfig.from_env().allow_network is False


def test_web_config_llm_router_default_matches_env(monkeypatch) -> None:
    monkeypatch.delenv("APPFORGE_LLM_ROUTER", raising=False)

    assert WebConfig().llm_router is WebConfig.from_env().llm_router


def test_web_app_does_not_register_oauth_routes(tmp_path: Path) -> None:
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )

    paths = {getattr(route, "path", "") for route in app.routes}

    assert not any("/oauth" in path for path in paths)


def _mock_ready_bridge(
    monkeypatch,
    *,
    provider: str = "deepseek",
    model: str = "deepseek-v4-pro",
) -> None:
    monkeypatch.setattr(llm_bridge, "ping", lambda url, **kwargs: {"ok": True})
    monkeypatch.setattr(llm_bridge, "get_active", lambda url: {"provider": provider, "model": model})
    monkeypatch.setattr(
        llm_bridge,
        "list_providers",
        lambda url: {
            "providers": [
                {
                    "id": provider,
                    "name": provider.title(),
                    "configured": True,
                    "has_key": True,
                    "key_source": "stored",
                    "models": [{"id": model}],
                }
            ]
        },
    )


class FixtureScriptDriver(AgentDriver):
    name = "fixture"

    def __init__(self) -> None:
        self.framework_root = Path(__file__).resolve().parents[1]
        self.fixture = self.framework_root / "tests" / "fixtures" / "fake_stage_agent.py"

    def run(self, prompt, *, layout, stage, attempt, timeout, cancel_event=None):  # type: ignore[no-untyped-def]
        final_path = layout.logs / f"{stage}-attempt-{attempt}-fixture-final.txt"
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


class BlockingDriver(AgentDriver):
    name = "blocking"

    def __init__(self) -> None:
        self.started = False
        self.cancel_seen = False

    def run(self, prompt, *, layout, stage, attempt, timeout, cancel_event=None):  # type: ignore[no-untyped-def]
        self.started = True
        final_path = layout.logs / f"{stage}-attempt-{attempt}-blocking-final.txt"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                self.cancel_seen = True
                final_path.write_text("", encoding="utf-8")
                return DriverResult(
                    False,
                    130,
                    "",
                    "Cancelled by user.",
                    0.01,
                    ["blocking"],
                    str(final_path),
                )
            time.sleep(0.02)
        final_path.write_text("", encoding="utf-8")
        return DriverResult(False, 124, "", "Timed out.", float(timeout), ["blocking"], str(final_path))


class ExitCodeDriver(AgentDriver):
    name = "exit-code-fixture"

    def run(self, prompt, *, layout, stage, attempt, timeout, cancel_event=None):  # type: ignore[no-untyped-def]
        final_path = layout.logs / f"{stage}-attempt-{attempt}-exit-final.txt"
        final_path.write_text("", encoding="utf-8")
        return DriverResult(False, 7, "", "fixture exit", 0.01, ["exit-code-fixture"], str(final_path))


class FailOnceFixtureDriver(FixtureScriptDriver):
    name = "fail-once-fixture"

    def __init__(self, failed_stage: str) -> None:
        super().__init__()
        self.failed_stage = failed_stage
        self.calls: list[str] = []

    def run(self, prompt, *, layout, stage, attempt, timeout, cancel_event=None):  # type: ignore[no-untyped-def]
        self.calls.append(stage)
        if stage == self.failed_stage and self.calls.count(stage) == 1:
            final_path = layout.logs / f"{stage}-attempt-{attempt}-fail-once-final.txt"
            final_path.write_text("", encoding="utf-8")
            return DriverResult(
                False,
                7,
                "",
                "intentional first-attempt failure",
                0.01,
                ["fail-once-fixture"],
                str(final_path),
            )
        return super().run(
            prompt,
            layout=layout,
            stage=stage,
            attempt=attempt,
            timeout=timeout,
            cancel_event=cancel_event,
        )


def _wait_for_terminal(client: TestClient, job_id: str, timeout: float = 45.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["terminal"]:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job did not reach a terminal state: {last}")


def _wait_until_job(client: TestClient, job_id: str, predicate, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"job did not reach expected state: {last}")


def test_minimal_web_ui_and_health(tmp_path: Path, monkeypatch) -> None:
    _mock_ready_bridge(monkeypatch)
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with _client(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "AppForge-LLM v7" in page.text
        assert "어떤 앱을 만들까요?" in page.text
        assert "완료 후 ZIP 다운로드" in page.text
        assert 'type="module"' in page.text

        asset_match = re.search(r'src="(/assets/[^"]+\.js)"', page.text)
        assert asset_match, page.text
        asset = client.get(asset_match.group(1))
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        assert "프롬프트 하나로 자율적으로 앱 생성." in asset.text
        assert "완전 자율 실행" in asset.text
        assert "앱 기획부터 ZIP까지." not in asset.text
        assert "계획형 AI 에이전트 파이프라인" not in asset.text

        manifest = client.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.json()["name"] == "AppForge-LLM v7"

        fallback = client.get("/jobs/local-refresh-test")
        assert fallback.status_code == 200
        assert "AppForge-LLM v7" in fallback.text

        missing_api = client.get("/api/not-found")
        assert missing_api.status_code == 404

        health = client.get("/api/health")
        assert health.status_code == 200
        payload = health.json()
        assert payload["ready"] is True
        assert payload["driver"]["selected"] == "llm-bridge"
        assert payload["prompt_max_chars"] == 20_000
        assert payload["safety"]["deployment_enabled"] is False


def test_web_ui_run_mode_picker_is_collapsed_by_default() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "src" / "components" / "ComposerCard.vue").read_text(
        encoding="utf-8"
    )
    catalog = (root / "frontend" / "src" / "i18n.ts").read_text(encoding="utf-8")

    assert '<details class="mode-switch">' in source
    summary = source.split("<summary>", 1)[1].split("</summary>", 1)[0]
    assert "t('composer.mode')" in summary
    assert "t('composer.autonomous')" in summary
    assert "'composer.mode': '실행 모드'" in catalog
    assert "'composer.autonomous': '완전 자율 실행'" in catalog


def test_job_creation_snapshots_model_and_generation_settings(tmp_path: Path, monkeypatch) -> None:
    _mock_ready_bridge(monkeypatch, provider="openai", model="gpt-default")
    manager = JobManager(_fixture_config(tmp_path))
    monkeypatch.setattr(manager, "_maybe_start_next_job_locked", lambda: None)

    created = manager.create_job(
        "설정이 고정되는 앱을 만들어라",
        pipeline_name="prototype",
        provider="openai",
        model="gpt-job",
        generation={"temperature": 0.2, "topP": 0.85, "maxTokens": 3072},
        pricing={"input": 1.0, "output": 2.0, "cache_read": 0.25},
    )

    assert created["llm"] == {
        "provider": "openai",
        "model": "gpt-job",
        "generation": {"temperature": 0.2, "topP": 0.85, "maxTokens": 3072},
        "pricing": {"input": 1.0, "output": 2.0, "cache_read": 0.25},
    }
    stored = json.loads(
        (manager.jobs_dir / f"{created['id']}.json").read_text(encoding="utf-8")
    )
    assert stored["llm"] == created["llm"]


def test_explicit_provider_does_not_inherit_an_unrelated_global_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "appforge.llm_bridge.get_active",
        lambda _url: {"provider": "openai", "model": "gpt-active"},
    )
    config = replace(_fixture_config(tmp_path), llm_provider="openai", model="gpt-global")
    manager = JobManager(config)
    monkeypatch.setattr(manager, "_maybe_start_next_job_locked", lambda: None)

    created = manager.create_job(
        "Anthropic 기본 모델로 앱을 만들어라",
        pipeline_name="prototype",
        provider="anthropic",
    )

    assert created["llm"]["provider"] == "anthropic"
    assert created["llm"]["model"] is None


def test_usage_events_accumulate_job_and_stage_cost(tmp_path: Path) -> None:
    manager = JobManager(_fixture_config(tmp_path))
    job = _minimal_job("usage-job")
    job["llm"] = {
        "provider": "openai",
        "model": "gpt-test",
        "generation": {},
        "pricing": {"input": 1.0, "output": 2.0, "cache_read": 0.25},
    }
    job["stages"] = [
        {
            "id": "implementation",
            "name": "앱 구현",
            "status": "running",
            "detail": "실행 중",
            "attempt": 1,
            "error": None,
        }
    ]
    with manager._lock:
        manager._jobs[str(job["id"])] = job

    manager._handle_runner_event(
        str(job["id"]),
        {
            "event": "usage",
            "stage": "implementation",
            "usage": {
                "inputTokens": 1_000,
                "outputTokens": 1_000,
                "cacheReadInputTokens": 200,
                "totalTokens": 2_000,
            },
        },
    )

    payload = manager.get_job(str(job["id"]))
    assert payload["usage"]["input_tokens"] == 1_000
    assert payload["usage"]["output_tokens"] == 1_000
    assert payload["usage"]["total_tokens"] == 2_000
    assert payload["usage"]["estimated_cost_usd"] == pytest.approx(0.00285)
    assert payload["stages"][0]["usage"] == payload["usage"]


def test_llm_text_events_are_coalesced_as_markdown(tmp_path: Path) -> None:
    manager = JobManager(_fixture_config(tmp_path))
    job = _minimal_job("markdown-job")
    job["stages"] = [
        {
            "id": "implementation",
            "name": "앱 구현",
            "status": "running",
            "detail": "실행 중",
            "attempt": 1,
            "error": None,
        }
    ]
    with manager._lock:
        manager._jobs[str(job["id"])] = job

    manager._handle_runner_event(
        str(job["id"]),
        {"event": "llm_text", "stage": "implementation", "delta": "## 설계\n"},
    )
    manager._handle_runner_event(
        str(job["id"]),
        {"event": "llm_text", "stage": "implementation", "delta": "```ts\nconst ok = true\n```"},
    )

    outputs = [event for event in manager.get_job(str(job["id"]))["events"] if event["event"] == "llm_output"]
    assert len(outputs) == 1
    assert outputs[0]["data"]["markdown"] == "## 설계\n```ts\nconst ok = true\n```"


def test_job_history_api_supports_pagination_star_archive_and_rerun(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = JobManager(_fixture_config(tmp_path))
    jobs = []
    for index in range(3):
        job = _minimal_job(f"history-{index}", status="failed")
        job["created_at"] = f"2026-07-0{index + 1}T00:00:00Z"
        job["updated_at"] = job["created_at"]
        job["pipeline"] = "prototype"
        job["mode"] = "autonomous"
        job["llm"] = {
            "provider": "openai",
            "model": f"gpt-{index}",
            "generation": {"temperature": 0.1 * index},
            "pricing": {},
        }
        jobs.append(job)
    with manager._lock:
        for job in jobs:
            manager._jobs[str(job["id"])] = job
            manager._save_locked(job)

    rerun_calls: list[tuple[str, dict]] = []

    def fake_create_job(prompt: str, **kwargs) -> dict:
        rerun_calls.append((prompt, kwargs))
        return {**_minimal_job("rerun-created", status="queued"), "terminal": False, "progress": 0}

    monkeypatch.setattr(manager, "create_job", fake_create_job)
    app = create_app(
        _fixture_config(tmp_path),
        manager=manager,
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with _client(app) as client:
        first = client.get("/api/jobs?limit=2")
        assert first.status_code == 200, first.text
        assert [item["id"] for item in first.json()["jobs"]] == ["history-2", "history-1"]
        assert first.json()["has_more"] is True
        cursor = first.json()["next_cursor"]
        second = client.get(f"/api/jobs?limit=2&cursor={cursor}")
        assert [item["id"] for item in second.json()["jobs"]] == ["history-0"]

        starred = client.patch("/api/jobs/history-1", json={"starred": True})
        assert starred.status_code == 200
        assert starred.json()["starred"] is True

        archived = client.patch("/api/jobs/history-0", json={"archived": True})
        assert archived.status_code == 200
        visible = client.get("/api/jobs?limit=10").json()["jobs"]
        assert "history-0" not in [item["id"] for item in visible]
        archived_list = client.get("/api/jobs?limit=10&archived=true").json()["jobs"]
        assert [item["id"] for item in archived_list] == ["history-0"]

        rerun = client.post("/api/jobs/history-2/rerun")
        assert rerun.status_code == 202
        assert rerun.json()["id"] == "rerun-created"

    assert rerun_calls == [
        (
            "테스트 앱",
            {
                "mode": "autonomous",
                "pipeline_name": "prototype",
                "provider": "openai",
                "model": "gpt-2",
                "generation": {"temperature": 0.2},
                "pricing": {},
            },
        )
    ]
    stored = json.loads((manager.jobs_dir / "history-1.json").read_text(encoding="utf-8"))
    assert stored["starred"] is True


def test_web_ui_error_panel_hides_internal_error_identifiers() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "src" / "components" / "ErrorPanel.vue").read_text(
        encoding="utf-8"
    )
    catalog = (root / "frontend" / "src" / "i18n.ts").read_text(encoding="utf-8")
    template = source.split("<template>", 1)[1]

    assert "props.error.stage_label" in template
    assert "{{ props.error.code" not in template
    assert "{{ props.error.stage }}" not in template
    assert "t('error.retryStage')" in template
    assert "'error.retryStage': '이 단계부터 재시도'" in catalog
    assert "이 스테이지부터 재시도" not in template


def test_web_job_runs_all_stages_and_enables_zip_download(tmp_path: Path, monkeypatch) -> None:
    _mock_ready_bridge(monkeypatch)
    monkeypatch.setattr("appforge.web_jobs.create_driver", lambda *args, **kwargs: FixtureScriptDriver())
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with _client(app) as client:
        created = client.post(
            "/api/jobs",
            json={
                "prompt": (
                    "빠른 MVP 프로토타입을 만들어라. 핵심 기능을 구현하고 테스트와 "
                    "다운로드 가능한 소스 ZIP까지 준비하라."
                )
            },
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]

        job = _wait_for_terminal(client, job_id)
        assert job["status"] == "completed", job.get("error")
        assert job["progress"] == 100
        assert job["download"]["available"] is True
        assert job["download"]["url"] == f"/api/jobs/{job_id}/download"
        assert all(stage["status"] == "completed" for stage in job["stages"])
        assert any(event["event"] == "job_completed" for event in job["events"])

        download = client.get(job["download"]["url"])
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        assert "attachment" in download.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            names = archive.namelist()
        assert "README.md" in names
        assert not any(name.startswith(".appforge/") for name in names)


def test_retry_failed_stage_completes_remaining_pipeline_before_packaging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _mock_ready_bridge(monkeypatch)
    driver = FailOnceFixtureDriver("implementation")
    monkeypatch.setattr("appforge.web_jobs.create_driver", lambda *args, **kwargs: driver)
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with _client(app) as client:
        created = client.post(
            "/api/jobs",
            json={
                "prompt": "중간 실패 후 재시도되는 MVP 프로토타입을 만들어라.",
                "pipeline": "prototype",
            },
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]

        failed = _wait_for_terminal(client, job_id)
        assert failed["status"] == "failed"
        assert failed["error"]["stage"] == "implementation"
        assert failed["error"]["stage_label"] == "앱 구현"
        assert failed["error"]["title"] == "앱 제작을 완료하지 못했습니다"
        assert failed["error"]["message"] == "앱 구현 중 문제가 발생해 작업을 완료하지 못했습니다."
        assert failed["message"] == failed["error"]["message"]
        public_failure = json.dumps(failed, ensure_ascii=False).casefold()
        assert "coding agent" not in public_failure
        assert "exit code" not in public_failure
        assert "intentional first-attempt failure" not in public_failure
        assert failed["download"]["available"] is False

        retried = client.post(f"/api/jobs/{job_id}/retry")
        assert retried.status_code == 202, retried.text
        completed = _wait_for_terminal(client, job_id)

    assert completed["status"] == "completed", completed.get("error")
    assert completed["download"]["available"] is True
    assert all(stage["status"] == "completed" for stage in completed["stages"])
    assert driver.calls.count("implementation") == 2
    assert "verification" in driver.calls
    assert "handoff" in driver.calls


def test_guided_approval_is_consumed_by_only_the_current_stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _mock_ready_bridge(monkeypatch)
    driver = FixtureScriptDriver()
    monkeypatch.setattr("appforge.web_jobs.create_driver", lambda *args, **kwargs: driver)
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    approval_stages = ["architecture", "experience", "release"]

    with _client(app) as client:
        created = client.post(
            "/api/jobs",
            json={
                "prompt": "단계별 승인이 필요한 웹앱을 만들어라.",
                "pipeline": "web-app",
                "mode": "guided",
            },
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]

        for expected_stage in approval_stages:
            awaiting = _wait_until_job(
                client,
                job_id,
                lambda job: job["status"] in {"awaiting_approval", "completed", "failed"},
            )
            assert awaiting["status"] == "awaiting_approval", awaiting.get("error")
            assert awaiting["active_stage"] == expected_stage
            approved = client.post(f"/api/jobs/{job_id}/approve")
            assert approved.status_code == 202, approved.text

        completed = _wait_for_terminal(client, job_id)

    assert completed["status"] == "completed", completed.get("error")
    assert completed["download"]["available"] is True


def test_archive_is_regenerated_from_current_workspace(
    tmp_path: Path,
) -> None:
    manager = JobManager(_fixture_config(tmp_path))
    layout = initialize_project(
        "Create a fresh archive",
        projects_dir=tmp_path / "projects",
        name="archive-freshness",
        pipeline_name="prototype",
    )
    source = layout.root / "app.txt"
    source.write_text("old\n", encoding="utf-8")

    first = manager._ensure_archive(layout)
    source.write_text("new\n", encoding="utf-8")
    second = manager._ensure_archive(layout)

    assert second != first
    with zipfile.ZipFile(second) as archive:
        assert archive.read("app.txt").decode("utf-8") == "new\n"


def test_preview_build_failure_does_not_reuse_stale_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = JobManager(_fixture_config(tmp_path))
    layout = initialize_project(
        "Build a preview",
        projects_dir=tmp_path / "projects",
        name="preview-freshness",
        pipeline_name="prototype",
    )
    stale_dist = layout.root / "dist"
    stale_dist.mkdir()
    (stale_dist / "index.html").write_text("<p>stale</p>\n", encoding="utf-8")
    job = _minimal_job("preview-failure", status="completed")
    job["project_path"] = str(layout.root)
    job["preview"] = {
        "available": True,
        "url": "/preview/preview-failure/",
        "path": str(stale_dist),
        "built_at": "2026-07-01T00:00:00Z",
    }
    with manager._lock:
        manager._jobs[str(job["id"])] = job
    monkeypatch.setattr(
        "appforge.web_jobs.RunBuildTool.run",
        lambda *args, **kwargs: ToolResult(success=False, error="intentional build failure"),
    )

    with pytest.raises(WebJobError) as captured:
        manager.build_preview(str(job["id"]))

    assert captured.value.code == "PREVIEW_BUILD_FAILED"
    assert manager.get_job(str(job["id"]))["preview"]["available"] is False


def test_revision_copy_skips_symbolic_links(tmp_path: Path) -> None:
    manager = JobManager(_fixture_config(tmp_path))
    source = tmp_path / "source-workspace"
    source.mkdir()
    (source / "app.txt").write_text("workspace\n", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must-not-copy\n", encoding="utf-8")
    try:
        (source / "external-link.txt").symlink_to(outside)
        (source / "internal-link.txt").symlink_to(source / "app.txt")
    except OSError:
        pytest.skip("symbolic links are not available in this test environment")
    target = tmp_path / "revision-workspace"

    manager._copy_workspace_for_revision(source, target)

    assert (target / "app.txt").read_text(encoding="utf-8") == "workspace\n"
    assert not (target / "external-link.txt").exists()
    assert not (target / "internal-link.txt").exists()
    assert all(
        "must-not-copy" not in path.read_text(encoding="utf-8", errors="ignore")
        for path in target.rglob("*")
        if path.is_file()
    )


def test_web_job_cancel_stops_active_driver(tmp_path: Path, monkeypatch) -> None:
    _mock_ready_bridge(monkeypatch)
    driver = BlockingDriver()
    monkeypatch.setattr("appforge.web_jobs.create_driver", lambda *args, **kwargs: driver)
    config = _fixture_config(tmp_path)
    app = create_app(config, llm_bridge_manager=_FakeBridgeManager(), session_token=SESSION_TOKEN)
    with _client(app) as client:
        created = client.post("/api/jobs", json={"prompt": "취소 가능한 간단한 웹앱을 만들어라"})
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]

        _wait_until_job(client, job_id, lambda _job: driver.started)

        cancelled = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        payload = cancelled.json()
        assert payload["status"] == "cancelled"
        assert payload["terminal"] is True
        assert payload["error"]["code"] == "JOB_CANCELLED"
        assert payload["download"]["available"] is False
        assert any(event["event"] == "job_cancelled" for event in payload["events"])

        health = client.get("/api/health").json()
        assert health["busy"] is False
        assert health["active_job_id"] is None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not driver.cancel_seen:
            time.sleep(0.02)
        assert driver.cancel_seen


def test_session_end_shuts_down_managers_and_schedules_process_stop(tmp_path: Path) -> None:
    callbacks: list[str] = []
    bridge_manager = _FakeBridgeManager()
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=bridge_manager,
        shutdown_callback=lambda: callbacks.append("stop"),
        session_token=SESSION_TOKEN,
    )
    with _client(app) as client:
        response = client.post("/api/session/end")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["closing"] is True
        assert "세션" in payload["message"]
        assert "max-age=0" in response.headers["set-cookie"].casefold()
        assert bridge_manager.shutdown_calls == 1
        assert client.get("/api/jobs").status_code == 403

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not callbacks:
            time.sleep(0.02)
        assert callbacks == ["stop"]


def test_mutating_api_requires_session_token(tmp_path: Path) -> None:
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/session/end")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_SESSION_TOKEN"


def test_one_time_bootstrap_exchanges_fragment_code_for_http_only_cookie(tmp_path: Path) -> None:
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
        bootstrap_token="one-time-bootstrap-code-0123456789abcdef",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/session/bootstrap",
            json={"code": "one-time-bootstrap-code-0123456789abcdef"},
            headers={"origin": "http://127.0.0.1"},
        )
        assert response.status_code == 200, response.text
        cookie = response.headers["set-cookie"].casefold()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "one-time-bootstrap-code" not in response.text

        authorized = client.get("/api/jobs")
        assert authorized.status_code == 200, authorized.text

        replay = client.post(
            "/api/session/bootstrap",
            json={"code": "one-time-bootstrap-code-0123456789abcdef"},
            headers={"origin": "http://127.0.0.1"},
        )
        assert replay.status_code == 403


def test_event_stream_rejects_legacy_query_token(tmp_path: Path) -> None:
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get(f"/api/jobs/missing/events?token={SESSION_TOKEN}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_SESSION_TOKEN"


def test_rejects_non_loopback_host(tmp_path: Path) -> None:
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/health", headers={"host": "attacker.test"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_HOST"


def test_rejects_cross_origin_post(tmp_path: Path) -> None:
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/session/end",
            headers={
                "origin": "https://evil.example",
                "X-AppForge-Token": SESSION_TOKEN,
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ORIGIN"


def test_rejects_a_different_loopback_origin_port(tmp_path: Path) -> None:
    app = create_app(
        _fixture_config(tmp_path),
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        response = client.post(
            "/api/session/end",
            headers={
                "origin": "http://127.0.0.1:9999",
                "X-AppForge-Token": SESSION_TOKEN,
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ORIGIN"


def test_web_job_returns_detailed_llm_setup_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(llm_bridge, "ping", lambda url, **kwargs: {"ok": True})
    monkeypatch.setattr(llm_bridge, "get_active", lambda url: {"provider": "openai", "model": "gpt-4o-mini"})
    monkeypatch.setattr(
        llm_bridge,
        "list_providers",
        lambda url: {
            "providers": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "configured": False,
                    "has_key": False,
                    "key_source": "none",
                    "models": [{"id": "gpt-4o-mini"}],
                }
            ]
        },
    )
    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        driver="llm-bridge",
        llm_bridge_url="http://bridge.test",
        allow_network=False,
        max_stage_attempts=1,
        stage_timeout=60,
    )
    app = create_app(config, llm_bridge_manager=_FakeBridgeManager(), session_token=SESSION_TOKEN)
    with _client(app) as client:
        health = client.get("/api/health").json()
        assert health["ready"] is False
        assert health["driver"]["action"]

        created = client.post("/api/jobs", json={"prompt": "간단한 웹앱을 만들어라"})
        assert created.status_code == 202
        job = _wait_for_terminal(client, created.json()["id"], timeout=10)
        assert job["status"] == "failed"
        assert job["error"]["code"] == "AGENT_NOT_AVAILABLE"
        assert job["error"]["title"]
        assert job["error"]["message"]
        assert job["error"]["action"]
        assert "technical" not in job["error"]
        assert job["download"]["available"] is False


def test_web_job_preserves_driver_exit_code_and_stage_details(tmp_path: Path, monkeypatch) -> None:
    _mock_ready_bridge(monkeypatch)
    monkeypatch.setattr("appforge.web_jobs.create_driver", lambda *args, **kwargs: ExitCodeDriver())
    config = _fixture_config(tmp_path)
    app = create_app(config, llm_bridge_manager=_FakeBridgeManager(), session_token=SESSION_TOKEN)
    with _client(app) as client:
        created = client.post("/api/jobs", json={"prompt": "간단한 웹앱을 만들어라"})
        assert created.status_code == 202
        job_id = created.json()["id"]
        job = _wait_for_terminal(client, job_id, timeout=10)
        assert job["status"] == "failed"
        error = job["error"]
        assert error["code"] == "AGENT_PROCESS_FAILED"
        assert error["stage"] == "intake"
        assert error["stage_label"] == "요청 정리"
        assert error["attempt"] == 1
        assert "technical" not in error
        assert error["action"]

    stored = json.loads((config.data_dir / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
    technical = stored["error"]["technical"]
    assert technical["driver"]["exit_code"] == 7
    assert technical["diagnostic_message"] == (
        "The coding agent exited with code 7 during stage intake."
    )
    assert "Review the agent output below" in technical["diagnostic_action"]


def test_llm_bridge_health_requires_configured_active_provider(tmp_path: Path, monkeypatch) -> None:
    provider_payload = {
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI",
                "configured": False,
                "has_key": False,
                "key_source": "none",
                "models": [{"id": "gpt-4o-mini"}],
            }
        ]
    }
    monkeypatch.setattr(llm_bridge, "ping", lambda url, **kwargs: {"ok": True})
    monkeypatch.setattr(llm_bridge, "get_active", lambda url: {"provider": "openai", "model": "gpt-4o-mini"})
    monkeypatch.setattr(llm_bridge, "list_providers", lambda url: provider_payload)

    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        driver="llm-bridge",
        llm_bridge_url="http://bridge.test",
    )
    app = create_app(config, llm_bridge_manager=_FakeBridgeManager(), session_token=SESSION_TOKEN)
    with _client(app) as client:
        health = client.get("/api/health").json()
        assert health["ready"] is False
        assert health["driver"]["selected"] is None
        assert "openai" in health["driver"]["message"]

        provider_payload["providers"][0]["configured"] = True
        provider_payload["providers"][0]["has_key"] = True
        provider_payload["providers"][0]["key_source"] = "stored"

        health = client.get("/api/health").json()
        assert health["ready"] is True
        assert health["driver"]["selected"] == "llm-bridge"
        assert "openai/gpt-4o-mini" in health["driver"]["message"]


def test_auto_driver_prefers_configured_llm_bridge(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(llm_bridge, "ping", lambda url, **kwargs: {"ok": True})
    monkeypatch.setattr(
        llm_bridge,
        "get_active",
        lambda url: {"provider": "deepseek", "model": "deepseek-v4-pro"},
    )
    monkeypatch.setattr(
        llm_bridge,
        "list_providers",
        lambda url: {
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "configured": True,
                    "has_key": True,
                    "key_source": "stored",
                    "models": [{"id": "deepseek-v4-pro"}],
                }
            ]
        },
    )

    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        driver="auto",
        llm_bridge_url="http://bridge.test",
    )
    app = create_app(config, llm_bridge_manager=_FakeBridgeManager(), session_token=SESSION_TOKEN)
    with _client(app) as client:
        health = client.get("/api/health").json()

    assert health["ready"] is True
    assert health["driver"]["requested"] == "auto"
    assert health["driver"]["selected"] == "llm-bridge-agent"
    assert health["driver"]["label"] == "LLM 브릿지 · deepseek"
    assert "deepseek/deepseek-v4-pro" in health["driver"]["message"]


def test_cli_or_command_driver_is_not_available_in_web_app(tmp_path: Path) -> None:
    for driver in ("codex", "generic"):
        config = WebConfig(
            projects_dir=tmp_path / f"projects-{driver}",
            data_dir=tmp_path / f"web-state-{driver}",
            driver=driver,
        )
        app = create_app(config, session_token=SESSION_TOKEN)
        with _client(app) as client:
            health = client.get("/api/health").json()

        assert health["ready"] is False
        assert health["driver"]["selected"] is None
        assert "지원하지" in health["driver"]["message"] or "더 이상 지원하지" in health["driver"]["message"]


def test_codex_driver_uses_removed_label(tmp_path: Path) -> None:
    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        driver="codex",
    )
    app = create_app(config, session_token=SESSION_TOKEN)
    with _client(app) as client:
        health = client.get("/api/health").json()

    assert health["ready"] is False
    assert health["driver"]["selected"] is None
    assert health["driver"]["label"] == "CLI 드라이버 제거됨"
    assert "codex" in health["driver"]["message"]


def test_llm_provider_api_proxies_to_bridge(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def remember(name: str, *args: object, **kwargs: object) -> None:
        calls.append((name, args, kwargs))

    def fake_list(url: str) -> dict:
        remember("list", url)
        return {
            "providers": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "kind": "api-key",
                    "configured": True,
                    "has_key": True,
                    "key_source": "stored",
                    "models": [{"id": "gpt-4o-mini"}],
                }
            ]
        }

    monkeypatch.setattr(llm_bridge, "list_providers", fake_list)
    monkeypatch.setattr(
        llm_bridge,
        "provider_models",
        lambda url, provider_id: (
            remember("models", url, provider_id)
            or {"id": provider_id, "name": "OpenAI", "models": [{"id": "gpt-4o-mini"}]}
        ),
    )
    monkeypatch.setattr(
        llm_bridge,
        "upsert_provider",
        lambda url, provider_id, **kwargs: (
            remember("upsert", url, provider_id, **kwargs)
            or {"status": {"id": provider_id, "name": "OpenAI", "configured": True}}
        ),
    )
    monkeypatch.setattr(
        llm_bridge,
        "test_provider",
        lambda url, provider_id, **kwargs: (
            remember("test", url, provider_id, **kwargs)
            or {"ok": True, "text": "ok", "provider": provider_id, "model": kwargs.get("model")}
        ),
    )
    monkeypatch.setattr(
        llm_bridge,
        "get_active",
        lambda url: remember("get_active", url) or {"provider": "openai", "model": "gpt-4o-mini"},
    )
    monkeypatch.setattr(
        llm_bridge,
        "set_active",
        lambda url, provider, model: (
            remember("set_active", url, provider, model) or {"provider": provider, "model": model}
        ),
    )
    monkeypatch.setattr(
        llm_bridge,
        "delete_provider",
        lambda url, provider_id: remember("delete", url, provider_id) or {"ok": True},
    )

    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        llm_bridge_url="http://bridge.test",
    )
    app = create_app(config, session_token=SESSION_TOKEN)
    with _client(app) as client:
        assert client.get("/api/llm/providers").json()["providers"][0]["id"] == "openai"
        assert client.get("/api/llm/providers/openai/models").json()["models"][0]["id"] == "gpt-4o-mini"
        saved = client.put(
            "/api/llm/providers/openai",
            json={"apiKey": "sk-test", "baseURL": "https://example.test/v1", "defaultModel": "gpt-4o-mini"},
        )
        assert saved.status_code == 200
        tested = client.post("/api/llm/providers/openai/test", json={"model": "gpt-4o-mini"})
        assert tested.json()["ok"] is True
        assert client.get("/api/llm/active").json()["provider"] == "openai"
        active = client.put("/api/llm/active", json={"provider": "openai", "model": "gpt-4o-mini"})
        assert active.json()["model"] == "gpt-4o-mini"
        assert client.delete("/api/llm/providers/openai").json()["ok"] is True

    assert ("list", ("http://bridge.test",), {}) in calls
    assert ("models", ("http://bridge.test", "openai"), {}) in calls
    assert any(call[0] == "upsert" and call[2]["api_key"] == "sk-test" for call in calls)
    assert any(call[0] == "test" and call[2]["model"] == "gpt-4o-mini" for call in calls)
    assert ("set_active", ("http://bridge.test", "openai", "gpt-4o-mini"), {}) in calls
    assert ("delete", ("http://bridge.test", "openai"), {}) in calls


def test_llm_provider_api_autostarts_local_bridge_and_retries(tmp_path: Path, monkeypatch) -> None:
    attempts = {"list": 0}

    def fake_list(url: str) -> dict:
        attempts["list"] += 1
        if attempts["list"] == 1:
            raise llm_bridge.BridgeError(
                "LLM 브릿지에 연결할 수 없습니다: [Errno 61] Connection refused"
            )
        return {
            "providers": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "kind": "api-key",
                    "configured": False,
                    "has_key": False,
                    "key_source": "none",
                    "models": [{"id": "gpt-4o-mini"}],
                }
            ]
        }

    monkeypatch.setattr(llm_bridge, "list_providers", fake_list)
    bridge_manager = _FakeBridgeManager()
    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        llm_bridge_url="http://127.0.0.1:8788",
    )
    app = create_app(config, llm_bridge_manager=bridge_manager, session_token=SESSION_TOKEN)
    with _client(app) as client:
        response = client.get("/api/llm/providers")

    assert response.status_code == 200, response.text
    assert response.json()["providers"][0]["id"] == "openai"
    assert attempts["list"] == 2
    assert bridge_manager.ensure_calls == [
        (
            "http://127.0.0.1:8788",
            "LLM 브릿지에 연결할 수 없습니다: [Errno 61] Connection refused",
        )
    ]
    assert bridge_manager.shutdown_calls == 1


def test_llm_provider_api_reports_bridge_autostart_guidance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        llm_bridge,
        "list_providers",
        lambda url: (_ for _ in ()).throw(
            llm_bridge.BridgeError("LLM 브릿지에 연결할 수 없습니다: [Errno 61] Connection refused")
        ),
    )
    bridge_manager = _FakeBridgeManager(
        llm_bridge.BridgeError(
            "Bun을 찾을 수 없어 LLM 브릿지를 자동 시작할 수 없습니다.",
            payload={
                "action": "Bun을 설치하거나 llm_bridge 서비스를 직접 실행한 뒤 다시 시도하세요.",
                "reason": "bun_missing",
            },
        )
    )
    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        llm_bridge_url="http://127.0.0.1:8788",
    )
    app = create_app(config, llm_bridge_manager=bridge_manager, session_token=SESSION_TOKEN)
    with _client(app) as client:
        response = client.get("/api/llm/providers")

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["message"] == "Bun을 찾을 수 없어 LLM 브릿지를 자동 시작할 수 없습니다."
    assert error["action"] == "Bun을 설치하거나 llm_bridge 서비스를 직접 실행한 뒤 다시 시도하세요."
    assert error["context"]["reason"] == "bun_missing"
    assert len(bridge_manager.ensure_calls) == 1


def test_llm_provider_api_does_not_autostart_bridge_http_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        llm_bridge,
        "provider_models",
        lambda url, provider_id: (_ for _ in ()).throw(
            llm_bridge.BridgeError(
                "Unknown provider 'missing'",
                status_code=404,
                payload={"error": {"code": "UNKNOWN_PROVIDER"}},
            )
        ),
    )
    bridge_manager = _FakeBridgeManager()
    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        llm_bridge_url="http://127.0.0.1:8788",
    )
    app = create_app(config, llm_bridge_manager=bridge_manager, session_token=SESSION_TOKEN)
    with _client(app) as client:
        response = client.get("/api/llm/providers/missing/models")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "LLM 브릿지 요청을 안전하게 처리하지 못했습니다. 연결 설정을 확인하세요."
    assert response.json()["error"]["context"] == {"bridge_code": "UNKNOWN_PROVIDER"}
    assert bridge_manager.ensure_calls == []


def test_bridge_http_error_does_not_echo_remote_secret_fields(tmp_path: Path, monkeypatch) -> None:
    leaked = "sk-proj-" + ("a" * 40)
    monkeypatch.setattr(
        llm_bridge,
        "provider_models",
        lambda url, provider_id: (_ for _ in ()).throw(
            llm_bridge.BridgeError(
                f"provider rejected {leaked}",
                status_code=502,
                payload={
                    "action": f"retry with {leaked}",
                    "reason": leaked,
                    "error": {"code": "PROVIDER_ERROR", "message": leaked},
                },
            )
        ),
    )
    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        llm_bridge_url="http://127.0.0.1:8788",
    )
    app = create_app(config, llm_bridge_manager=_FakeBridgeManager(), session_token=SESSION_TOKEN)

    with _client(app) as client:
        response = client.get("/api/llm/providers/openai/models")

    assert response.status_code == 502
    assert leaked not in response.text
    assert response.json()["error"]["context"] == {"bridge_code": "PROVIDER_ERROR"}


def test_unexpected_error_response_is_generic(tmp_path: Path) -> None:
    config = _fixture_config(tmp_path)
    manager = JobManager(config)

    def broken_health() -> dict:
        raise ValueError("secret traceback /Users/lee/private/project.py")

    manager.health = broken_health  # type: ignore[method-assign]
    app = create_app(
        config,
        manager=manager,
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with _client(app, raise_server_exceptions=False) as client:
        response = client.get("/api/health")

    assert response.status_code == 500
    assert "ValueError" not in response.text
    assert "/Users/lee" not in response.text
    assert "secret traceback" not in response.text
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"


def test_public_job_error_excludes_technical_traceback(tmp_path: Path) -> None:
    config = _fixture_config(tmp_path)
    manager = JobManager(config)
    job = _minimal_job("redacted-job", status="failed")
    job["error"] = {
        "code": "UNEXPECTED_ERROR",
        "title": "실패",
        "message": "요청 실패",
        "action": "다시 시도하세요.",
        "technical": {
            "traceback": "Traceback at /Users/lee/private/project.py",
            "exception": "ValueError: secret",
        },
    }
    job["stages"] = [
        {
            "id": "implementation",
            "status": "failed",
            "error": {
                "code": "STAGE_FAILED",
                "title": "단계 실패",
                "message": "단계 실패",
                "action": "확인하세요.",
                "technical": {"traceback": "Traceback"},
            },
        }
    ]
    job["events"] = [
        {
            "event": "job_failed",
            "message": "실패",
            "timestamp": "2026-07-05T00:00:00Z",
            "data": {
                "traceback": "Traceback",
                "exception": "ValueError",
                "technical": {"path": "/Users/lee/private/project.py"},
            },
        }
    ]
    with manager._lock:
        manager._jobs[str(job["id"])] = job

    app = create_app(
        config,
        manager=manager,
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with _client(app) as client:
        response = client.get(f"/api/jobs/{job['id']}")

    assert response.status_code == 200
    payload = response.json()
    assert "technical" not in payload["error"]
    assert "technical" not in payload["stages"][0]["error"]
    rendered = str(payload)
    assert "Traceback" not in rendered
    assert "ValueError" not in rendered
    assert "/Users/lee" not in rendered


def test_job_event_subscription_replays_after_last_event_id(tmp_path: Path) -> None:
    manager = JobManager(_fixture_config(tmp_path))
    job = _minimal_job("event-job")
    with manager._lock:
        manager._jobs[str(job["id"])] = job
        manager._record_event_locked(job, "stage_started", "첫 이벤트")
        manager._record_event_locked(job, "job_completed", "완료")

    subscriber = manager.subscribe_events(str(job["id"]), last_event_id=1)
    try:
        item = subscriber.get_nowait()
    finally:
        manager.unsubscribe_events(str(job["id"]), subscriber)

    assert item["id"] == 2
    assert item["event"] == "job_completed"


def test_subscriber_queue_full_emits_gap_event(tmp_path: Path) -> None:
    manager = JobManager(_fixture_config(tmp_path))
    subscriber: queue.Queue[dict] = queue.Queue(maxsize=1)

    manager._put_subscriber_event(
        subscriber,
        {"id": 1, "event": "stage_started", "message": "첫 이벤트", "timestamp": "now"},
    )
    manager._put_subscriber_event(
        subscriber,
        {"id": 2, "event": "stage_completed", "message": "두 번째 이벤트", "timestamp": "now"},
    )

    item = subscriber.get_nowait()
    assert item["event"] == "event_gap"
    assert item["id"] == 2
    assert item["data"]["reason"] == "subscriber_queue_full"


def test_preview_response_uses_sandbox_csp(tmp_path: Path) -> None:
    config = _fixture_config(tmp_path)
    manager = JobManager(config)
    preview_root = tmp_path / "preview"
    preview_root.mkdir()
    (preview_root / "index.html").write_text(
        "<script>fetch('/api/health')</script>",
        encoding="utf-8",
    )
    job = _minimal_job("preview-job", status="completed")
    job["preview"] = {
        "available": True,
        "url": "/preview/preview-job/",
        "path": str(preview_root),
        "built_at": "2026-07-05T00:00:00Z",
    }
    with manager._lock:
        manager._jobs[str(job["id"])] = job

    app = create_app(
        config,
        manager=manager,
        llm_bridge_manager=_FakeBridgeManager(),
        session_token=SESSION_TOKEN,
    )
    with _client(app) as client:
        response = client.get("/preview/preview-job/")

    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "sandbox allow-scripts allow-forms" in csp
    assert "connect-src 'none'" in csp
    assert "unsafe-eval" not in csp
