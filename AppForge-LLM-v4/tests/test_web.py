from __future__ import annotations

import io
import re
import sys
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from appforge.web import create_app
from appforge.web_jobs import WebConfig


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
