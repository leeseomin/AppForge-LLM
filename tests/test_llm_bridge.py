from __future__ import annotations

import json
import threading

import pytest

from appforge import llm_bridge
from appforge.llm_bridge_process import (
    LLMBridgeProcessManager,
    _bridge_bind,
    _bridge_environment,
)


class _FakeSocket:
    def __init__(self, connection: "_BlockingConnection") -> None:
        self.connection = connection

    def shutdown(self, _how: int) -> None:
        self.connection.shutdown_called.set()
        self.connection.close()


class _BlockingConnection:
    def __init__(self) -> None:
        self.request_seen = threading.Event()
        self.response_waiting = threading.Event()
        self.closed = threading.Event()
        self.shutdown_called = threading.Event()
        self.sock = _FakeSocket(self)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        assert method == "POST"
        assert path == "/generate"
        assert body is not None
        assert headers["Content-Type"] == "application/json"
        assert headers["X-AppForge-Bridge-Token"] == "bridge-test-token-0123456789abcdef0123456789"
        self.request_seen.set()

    def getresponse(self) -> object:
        self.response_waiting.set()
        self.closed.wait(timeout=5)
        raise OSError("connection closed")

    def close(self) -> None:
        self.closed.set()


def test_generate_cancels_in_flight_bridge_http_request(monkeypatch) -> None:
    connection = _BlockingConnection()
    monkeypatch.setattr(
        llm_bridge.http.client,
        "HTTPConnection",
        lambda *args, **kwargs: connection,
    )
    monkeypatch.setenv(
        "APPFORGE_LLM_BRIDGE_TOKEN",
        "bridge-test-token-0123456789abcdef0123456789",
    )
    cancel_event = threading.Event()
    errors: list[BaseException] = []

    def call_bridge() -> None:
        try:
            llm_bridge.generate(
                "http://127.0.0.1:8788",
                prompt="Generate a tiny app",
                timeout=30,
                cancel_event=cancel_event,
            )
        except BaseException as exc:  # pragma: no cover - asserted from caller thread
            errors.append(exc)

    worker = threading.Thread(target=call_bridge)
    worker.start()
    assert connection.request_seen.wait(timeout=2)
    assert connection.response_waiting.wait(timeout=2)

    cancel_event.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert connection.shutdown_called.is_set()
    assert len(errors) == 1
    assert isinstance(errors[0], llm_bridge.BridgeCancelled)


def test_plain_http_bridge_is_rejected_when_it_is_not_loopback(monkeypatch) -> None:
    monkeypatch.setenv(
        "APPFORGE_LLM_BRIDGE_TOKEN",
        "bridge-test-token-0123456789abcdef0123456789",
    )
    monkeypatch.setattr(
        llm_bridge.http.client,
        "HTTPConnection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network must not open")),
    )

    with pytest.raises(llm_bridge.BridgeError) as caught:
        llm_bridge.list_providers("http://bridge.example")

    assert caught.value.code == "INSECURE_BRIDGE_URL"


def test_bridge_process_environment_does_not_forward_arbitrary_host_secrets(monkeypatch) -> None:
    monkeypatch.setenv("UNRELATED_DATABASE_PASSWORD", "must-not-cross-boundary")
    monkeypatch.setenv("OPENAI_API_KEY", "allowed-provider-key")
    removed_provider_keys = {
        "GROQ_API_KEY",
        "CEREBRAS_API_KEY",
        "TOGETHERAI_API_KEY",
        "FIREWORKS_API_KEY",
        "DEEPINFRA_API_KEY",
    }
    for key in removed_provider_keys:
        monkeypatch.setenv(key, "removed-provider-key")

    env = _bridge_environment("127.0.0.1", "8788", "bridge-capability")

    assert "UNRELATED_DATABASE_PASSWORD" not in env
    assert removed_provider_keys.isdisjoint(env)
    assert env["OPENAI_API_KEY"] == "allowed-provider-key"
    assert env["APPFORGE_LLM_BRIDGE_TOKEN"] == "bridge-capability"


def test_disabled_autostart_reuses_an_authenticated_existing_bridge(monkeypatch, tmp_path) -> None:
    base_url = "http://127.0.0.1:8788"
    manager = LLMBridgeProcessManager(runtime_dir=tmp_path, enabled=False)
    monkeypatch.setattr(manager, "_is_healthy", lambda _base_url: True)

    manager.ensure_running(base_url)

    headers = llm_bridge._request_headers(base_url, "/ready", "application/json")
    assert headers[llm_bridge.BRIDGE_TOKEN_HEADER] == manager.auth_token
    manager.shutdown()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://user:secret@127.0.0.1:8788",
        "http://127.0.0.1:8788/?token=secret",
        "http://127.0.0.1:8788/prefix",
    ],
)
def test_bridge_autostart_rejects_credential_bearing_or_prefixed_urls(base_url) -> None:
    with pytest.raises(llm_bridge.BridgeError) as caught:
        _bridge_bind(base_url)

    assert caught.value.payload.get("reason") == "invalid_local_bridge_url"
    assert "secret" not in str(caught.value.payload)


def test_generate_normalizes_json_schema_response_format(monkeypatch) -> None:
    requests: list[dict] = []

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        *,
        body=None,
        timeout: float = 30.0,
        cancel_event=None,
    ) -> dict:
        requests.append(
            {
                "base_url": base_url,
                "method": method,
                "path": path,
                "body": json.loads(json.dumps(body)),
                "timeout": timeout,
                "cancel_event": cancel_event,
            }
        )
        return {"text": "{}"}

    monkeypatch.setattr(llm_bridge, "_request", fake_request)

    llm_bridge.generate(
        "http://bridge.test",
        prompt="Return JSON",
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "appforge_intake_envelope",
                "strict": False,
                "schema": {
                    "type": "object",
                    "required": ["artifacts", "stage_result", "files"],
                    "properties": {
                        "artifacts": {"type": "object"},
                        "stage_result": {"type": "object"},
                        "files": {"type": "object"},
                    },
                },
            },
        },
    )

    assert requests[0]["path"] == "/generate"
    assert requests[0]["body"]["responseFormat"] == {
        "type": "json",
        "schema": {
            "type": "object",
            "required": ["artifacts", "stage_result", "files"],
            "properties": {
                "artifacts": {"type": "object"},
                "stage_result": {"type": "object"},
                "files": {"type": "object"},
            },
        },
    }


def test_generate_forwards_all_supported_generation_options(monkeypatch) -> None:
    requests: list[dict] = []

    def fake_request(
        base_url: str,
        method: str,
        path: str,
        *,
        body=None,
        timeout: float = 30.0,
        cancel_event=None,
    ) -> dict:
        requests.append(json.loads(json.dumps(body)))
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(llm_bridge, "_request", fake_request)

    llm_bridge.generate(
        "http://bridge.test",
        prompt="Generate",
        max_tokens=2048,
        temperature=0.35,
        top_p=0.82,
    )

    assert requests[0]["generation"] == {
        "maxTokens": 2048,
        "temperature": 0.35,
        "topP": 0.82,
    }


def test_terminal_sse_rejects_connection_closed_before_done(monkeypatch) -> None:
    def incomplete_stream(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield {"type": "connected"}
        yield {"type": "text_delta", "text": "partial"}

    monkeypatch.setattr(llm_bridge, "_sse_request", incomplete_stream)

    try:
        list(llm_bridge.agent_events("http://bridge.test", "session-1", timeout=5))
    except llm_bridge.BridgeError as exc:
        assert exc.code == "BRIDGE_STREAM_INTERRUPTED"
        assert exc.status_code == 502
    else:  # pragma: no cover - protects the recovery contract
        raise AssertionError("early SSE EOF must be reported as an interruption")


def test_retryable_bridge_error_classification() -> None:
    transient = llm_bridge.BridgeError(
        "connection ended",
        status_code=502,
        payload={"error": {"code": "BRIDGE_STREAM_INTERRUPTED"}},
    )
    permanent = llm_bridge.BridgeError(
        "invalid request",
        status_code=422,
        payload={"error": {"code": "INVALID_REQUEST"}},
    )

    assert llm_bridge.is_retryable_error(transient) is True
    assert llm_bridge.is_retryable_error(permanent) is False
    assert llm_bridge.is_retryable_error(llm_bridge.BridgeCancelled()) is False
