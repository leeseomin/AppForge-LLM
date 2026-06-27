from __future__ import annotations

import io
import re
import sys
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from appforge import llm_bridge
from appforge.web import create_app
from appforge.web_jobs import WebConfig


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
    framework_root = Path(__file__).resolve().parents[1]
    fixture = framework_root / "tests" / "fixtures" / "fake_stage_agent.py"
    command = f'{sys.executable} -S "{fixture}" {{workspace}} {{stage}} "{framework_root}"'
    return WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        driver="generic",
        agent_cmd=command,
        allow_network=False,
        max_stage_attempts=1,
        stage_timeout=120,
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


def test_minimal_web_ui_and_health(tmp_path: Path) -> None:
    app = create_app(_fixture_config(tmp_path))
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "AppForge-LLM v4" in page.text
        assert "어떤 앱을 만들까요?" in page.text
        assert "완료 후 ZIP 다운로드" in page.text
        assert 'type="module"' in page.text

        asset_match = re.search(r'src="(/assets/[^"]+\.js)"', page.text)
        assert asset_match, page.text
        asset = client.get(asset_match.group(1))
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]

        manifest = client.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.json()["name"] == "AppForge-LLM v4"

        fallback = client.get("/jobs/local-refresh-test")
        assert fallback.status_code == 200
        assert "AppForge-LLM v4" in fallback.text

        missing_api = client.get("/api/not-found")
        assert missing_api.status_code == 404

        health = client.get("/api/health")
        assert health.status_code == 200
        payload = health.json()
        assert payload["ready"] is True
        assert payload["driver"]["selected"] == "generic"
        assert payload["prompt_max_chars"] == 20_000
        assert payload["safety"]["deployment_enabled"] is False


def test_web_job_runs_all_stages_and_enables_zip_download(tmp_path: Path) -> None:
    app = create_app(_fixture_config(tmp_path))
    with TestClient(app) as client:
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


def test_web_job_returns_detailed_agent_setup_error(tmp_path: Path) -> None:
    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        driver="generic",
        agent_cmd=None,
        allow_network=False,
        max_stage_attempts=1,
        stage_timeout=60,
    )
    app = create_app(config)
    with TestClient(app) as client:
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
        assert job["error"]["technical"]["driver"]["ready"] is False
        assert job["download"]["available"] is False


def test_web_job_preserves_agent_exit_code_and_stage_details(tmp_path: Path) -> None:
    config = WebConfig(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "web-state",
        driver="generic",
        agent_cmd='/bin/sh -c "exit 7"',
        allow_network=False,
        max_stage_attempts=1,
        stage_timeout=60,
    )
    app = create_app(config)
    with TestClient(app) as client:
        created = client.post("/api/jobs", json={"prompt": "간단한 웹앱을 만들어라"})
        assert created.status_code == 202
        job = _wait_for_terminal(client, created.json()["id"], timeout=10)
        assert job["status"] == "failed"
        error = job["error"]
        assert error["code"] == "AGENT_PROCESS_FAILED"
        assert error["stage"] == "intake"
        assert error["attempt"] == 1
        assert error["technical"]["driver"]["exit_code"] == 7
        assert error["technical"]["failed_checks"]
        assert error["action"]


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
    app = create_app(config)
    with TestClient(app) as client:
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
    app = create_app(config)
    with TestClient(app) as client:
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
    app = create_app(config, llm_bridge_manager=bridge_manager)
    with TestClient(app) as client:
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
    app = create_app(config, llm_bridge_manager=bridge_manager)
    with TestClient(app) as client:
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
    app = create_app(config, llm_bridge_manager=bridge_manager)
    with TestClient(app) as client:
        response = client.get("/api/llm/providers/missing/models")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Unknown provider 'missing'"
    assert bridge_manager.ensure_calls == []
