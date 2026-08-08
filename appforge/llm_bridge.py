"""HTTP client for the local AppForge LLM bridge.

The bridge is a Node/Bun service (``llm_bridge/``) that reuses the coco
``@opencode-ai/llm`` engine to connect to many external LLM providers
(OpenAI, Anthropic, Google Gemini, OpenRouter, xAI, DeepSeek, Groq, ...).

The Python web server proxies provider management and generation through this
module so the browser SPA stays same-origin, and the pipeline driver
(:class:`appforge.drivers.LLMBridgeDriver`) calls :func:`generate` to run a
configured model for a stage.

Only the standard library is used to avoid adding a new runtime dependency.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from typing import Any, Iterator
from urllib.parse import quote, urlsplit


class BridgeError(RuntimeError):
    """Raised when the bridge is unreachable or returns an error payload."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}

    @property
    def code(self) -> str:
        error = self.payload.get("error") if isinstance(self.payload, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return str(error["code"])
        code = self.payload.get("code") if isinstance(self.payload, dict) else None
        return str(code) if isinstance(code, str) else ""


class BridgeCancelled(BridgeError):
    """Raised when an in-flight bridge request is cancelled by the caller."""

    def __init__(self, message: str = "LLM bridge request cancelled.") -> None:
        super().__init__(
            message,
            status_code=499,
            payload={"error": {"code": "BRIDGE_REQUEST_CANCELLED", "message": message}},
        )


def _normalize_response_format(response_format: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert OpenAI-style JSON schema hints to the local bridge contract."""
    if not isinstance(response_format, dict):
        return response_format
    if response_format.get("type") != "json_schema":
        return response_format
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return response_format
    schema = json_schema.get("schema")
    if not isinstance(schema, dict):
        return response_format
    return {"type": "json", "schema": schema}


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    body: Any | None = None,
    timeout: float = 30.0,
    cancel_event: threading.Event | None = None,
) -> Any:
    if cancel_event is not None and cancel_event.is_set():
        raise BridgeCancelled()

    parsed_url = urlsplit(base_url.rstrip("/"))
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise BridgeError(f"Invalid LLM bridge URL: {base_url}")

    root_path = parsed_url.path.rstrip("/")
    request_path = f"{root_path}/{path.lstrip('/')}" or "/"
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    connection_class = (
        http.client.HTTPSConnection
        if parsed_url.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_class(parsed_url.hostname, parsed_url.port, timeout=timeout)
    done = threading.Event()
    cancelled = False

    def abort_on_cancel() -> None:
        nonlocal cancelled
        if cancel_event is None:
            return
        while not done.is_set():
            if not cancel_event.wait(0.05):
                continue
            cancelled = True
            try:
                sock = getattr(connection, "sock", None)
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
            return

    watcher: threading.Thread | None = None
    if cancel_event is not None:
        watcher = threading.Thread(target=abort_on_cancel, daemon=True)
        watcher.start()

    try:
        connection.request(method, request_path, body=data, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        if response.status >= 400:
            parsed: dict[str, Any] | None = None
            try:
                decoded = json.loads(raw) if raw else None
                parsed = decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                parsed = None
            error_payload = parsed.get("error") if parsed else None
            message = error_payload.get("message") if isinstance(error_payload, dict) else raw
            raise BridgeError(
                str(message or f"bridge returned HTTP {response.status}"),
                status_code=response.status,
                payload=parsed,
            )
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        if cancelled or (cancel_event is not None and cancel_event.is_set()):
            raise BridgeCancelled() from exc
        reason = getattr(exc, "reason", None) or str(exc)
        is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(reason).casefold()
        code = "BRIDGE_REQUEST_TIMEOUT" if is_timeout else "BRIDGE_CONNECTION_ERROR"
        status_code = 504 if is_timeout else 0
        raise BridgeError(
            f"LLM 브릿지 요청이 시간 초과되었습니다: {reason}"
            if is_timeout
            else f"LLM 브릿지에 연결할 수 없습니다: {reason}",
            status_code=status_code,
            payload={"error": {"code": code, "message": str(reason)}},
        ) from exc
    finally:
        done.set()
        connection.close()
        if watcher is not None:
            watcher.join(timeout=0.2)

    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError("LLM 브릿지 응답이 올바른 JSON이 아닙니다.") from exc


def _open_connection(base_url: str, timeout: float) -> tuple[http.client.HTTPConnection | http.client.HTTPSConnection, str]:
    parsed_url = urlsplit(base_url.rstrip("/"))
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise BridgeError(f"Invalid LLM bridge URL: {base_url}")
    connection_class = http.client.HTTPSConnection if parsed_url.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed_url.hostname, parsed_url.port, timeout=timeout)
    return connection, parsed_url.path.rstrip("/")


def _sse_request(
    base_url: str,
    method: str,
    path: str,
    *,
    body: Any | None = None,
    timeout: float = 600.0,
    cancel_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    if cancel_event is not None and cancel_event.is_set():
        raise BridgeCancelled()
    connection, root_path = _open_connection(base_url, timeout)
    request_path = f"{root_path}/{path.lstrip('/')}" or "/"
    data: bytes | None = None
    headers = {"Accept": "text/event-stream"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    done = threading.Event()
    cancelled = False
    timed_out = False

    def abort_on_cancel() -> None:
        nonlocal cancelled
        if cancel_event is None:
            return
        while not done.is_set():
            if not cancel_event.wait(0.05):
                continue
            cancelled = True
            try:
                sock = getattr(connection, "sock", None)
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
            return

    watcher: threading.Thread | None = None
    if cancel_event is not None:
        watcher = threading.Thread(target=abort_on_cancel, daemon=True)
        watcher.start()

    def abort_on_timeout() -> None:
        nonlocal timed_out
        if timeout <= 0 or done.wait(timeout):
            return
        timed_out = True
        try:
            sock = getattr(connection, "sock", None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()

    deadline_watcher = threading.Thread(target=abort_on_timeout, daemon=True)
    deadline_watcher.start()
    try:
        connection.request(method, request_path, body=data, headers=headers)
        response = connection.getresponse()
        if response.status >= 400:
            raw = response.read().decode("utf-8", errors="replace")
            parsed: dict[str, Any] | None = None
            try:
                decoded = json.loads(raw) if raw else None
                parsed = decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                parsed = None
            error_payload = parsed.get("error") if parsed else None
            message = error_payload.get("message") if isinstance(error_payload, dict) else raw
            raise BridgeError(
                str(message or f"bridge returned HTTP {response.status}"),
                status_code=response.status,
                payload=parsed,
            )
        event_name = "message"
        data_lines: list[str] = []
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise BridgeCancelled()
            raw_line = response.readline()
            if raw_line == b"":
                if timed_out:
                    raise BridgeError(
                        f"LLM 브릿지 SSE 요청이 {timeout:g}초 제한을 초과했습니다.",
                        status_code=504,
                        payload={
                            "error": {
                                "code": "BRIDGE_STREAM_TIMEOUT",
                                "message": f"SSE request exceeded {timeout:g} seconds",
                            }
                        },
                    )
                if data_lines:
                    payload = _decode_sse_payload(event_name, data_lines)
                    if payload is not None:
                        yield payload
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                payload = _decode_sse_payload(event_name, data_lines)
                event_name = "message"
                data_lines = []
                if payload is not None:
                    yield payload
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value or "message"
            elif field == "data":
                data_lines.append(value)
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        if cancelled or (cancel_event is not None and cancel_event.is_set()):
            raise BridgeCancelled() from exc
        if timed_out:
            raise BridgeError(
                f"LLM 브릿지 SSE 요청이 {timeout:g}초 제한을 초과했습니다.",
                status_code=504,
                payload={
                    "error": {
                        "code": "BRIDGE_STREAM_TIMEOUT",
                        "message": f"SSE request exceeded {timeout:g} seconds",
                    }
                },
            ) from exc
        reason = getattr(exc, "reason", None) or str(exc)
        is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(reason).casefold()
        code = "BRIDGE_STREAM_TIMEOUT" if is_timeout else "BRIDGE_STREAM_CONNECTION_ERROR"
        raise BridgeError(
            f"LLM 브릿지 SSE 요청이 시간 초과되었습니다: {reason}"
            if is_timeout
            else f"LLM 브릿지 SSE에 연결할 수 없습니다: {reason}",
            status_code=504 if is_timeout else 0,
            payload={"error": {"code": code, "message": str(reason)}},
        ) from exc
    finally:
        done.set()
        connection.close()
        if watcher is not None:
            watcher.join(timeout=0.2)
        deadline_watcher.join(timeout=0.2)


def _decode_sse_payload(event_name: str, data_lines: list[str]) -> dict[str, Any] | None:
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"text": raw}
    if isinstance(data, dict):
        data.setdefault("type", event_name)
        return data
    return {"type": event_name, "data": data}


def _terminal_sse_request(
    base_url: str,
    method: str,
    path: str,
    *,
    body: Any | None = None,
    timeout: float = 600.0,
    cancel_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield SSE events and reject a connection that ends without a terminal event."""

    terminal_seen = False
    for event in _sse_request(
        base_url,
        method,
        path,
        body=body,
        timeout=timeout,
        cancel_event=cancel_event,
    ):
        event_type = str(event.get("type") or event.get("event") or "").replace("-", "_")
        if event_type in {"done", "error", "cancelled"}:
            terminal_seen = True
        yield event
    if not terminal_seen:
        raise BridgeError(
            "LLM 브릿지 스트림이 완료 신호 없이 종료되었습니다. 자동 복구를 시도할 수 있습니다.",
            status_code=502,
            payload={
                "error": {
                    "code": "BRIDGE_STREAM_INTERRUPTED",
                    "message": "SSE connection closed before done/error/cancelled",
                }
            },
        )


_RETRYABLE_BRIDGE_CODES = {
    "BRIDGE_CONNECTION_ERROR",
    "BRIDGE_REQUEST_TIMEOUT",
    "BRIDGE_STREAM_CONNECTION_ERROR",
    "BRIDGE_STREAM_INTERRUPTED",
    "BRIDGE_STREAM_TIMEOUT",
    "AGENT_STREAM_ALREADY_ACTIVE",
}


def is_retryable_error(error: BridgeError) -> bool:
    """Return whether a bridge failure is likely transient and safe to retry."""

    if isinstance(error, BridgeCancelled):
        return False
    if error.code in _RETRYABLE_BRIDGE_CODES:
        return True
    if error.status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    message = str(error).casefold()
    transient_markers = (
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "remote end closed",
        "temporarily unavailable",
        "service unavailable",
        "internal server error",
        "too many requests",
        "rate limit",
        "overloaded",
        "network error",
        "fetch failed",
        "econnreset",
        "econnrefused",
        "premature eof",
        "broken pipe",
        "완료 신호 없이",
        "시간 초과",
        "연결할 수 없습니다",
    )
    return any(marker in message for marker in transient_markers)


def sleep_with_cancel(seconds: float, cancel_event: threading.Event | None = None) -> None:
    """Sleep for retry backoff while remaining responsive to cancellation."""

    if seconds <= 0:
        return
    if cancel_event is None:
        time.sleep(seconds)
        return
    if cancel_event.wait(seconds):
        raise BridgeCancelled()


def ping(base_url: str, *, timeout: float = 2.0) -> dict[str, Any]:
    """Return bridge ``/health`` payload, raising :class:`BridgeError` if down."""
    return _request(base_url, "GET", "/health", timeout=timeout)


def list_providers(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    return _request(base_url, "GET", "/providers", timeout=timeout)


def provider_models(base_url: str, provider_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
    return _request(base_url, "GET", f"/providers/{quote(provider_id)}/models", timeout=timeout)


def upsert_provider(
    base_url: str,
    provider_id: str,
    *,
    api_key: str | None = None,
    base_url_override: str | None = None,
    default_model: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if api_key is not None:
        body["apiKey"] = api_key
    if base_url_override is not None:
        body["baseURL"] = base_url_override
    if default_model is not None:
        body["defaultModel"] = default_model
    return _request(base_url, "PUT", f"/providers/{quote(provider_id)}", body=body, timeout=timeout)


def delete_provider(base_url: str, provider_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
    return _request(base_url, "DELETE", f"/providers/{quote(provider_id)}", timeout=timeout)


def test_provider(
    base_url: str,
    provider_id: str,
    *,
    api_key: str | None = None,
    base_url_override: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if api_key is not None:
        body["apiKey"] = api_key
    if base_url_override is not None:
        body["baseURL"] = base_url_override
    if model is not None:
        body["model"] = model
    return _request(base_url, "POST", f"/providers/{quote(provider_id)}/test", body=body, timeout=timeout)


def get_active(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    return _request(base_url, "GET", "/active", timeout=timeout)


def refresh_catalog(base_url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    return _request(base_url, "POST", "/catalog/refresh", timeout=timeout)


def set_active(base_url: str, provider: str | None, model: str | None, *, timeout: float = 5.0) -> dict[str, Any]:
    return _request(base_url, "PUT", "/active", body={"provider": provider, "model": model}, timeout=timeout)


def generate(
    base_url: str,
    *,
    prompt: str,
    system: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    response_format: dict[str, Any] | None = None,
    timeout: float = 600.0,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"prompt": prompt}
    normalized_response_format = _normalize_response_format(response_format)
    if system is not None:
        body["system"] = system
    if provider is not None:
        body["provider"] = provider
    if model is not None:
        body["model"] = model
    if normalized_response_format is not None:
        body["responseFormat"] = normalized_response_format
    generation: dict[str, Any] = {}
    if max_tokens is not None:
        generation["maxTokens"] = max_tokens
    if temperature is not None:
        generation["temperature"] = temperature
    if top_p is not None:
        generation["topP"] = top_p
    if generation:
        body["generation"] = generation
    return _request(
        base_url,
        "POST",
        "/generate",
        body=body,
        timeout=timeout,
        cancel_event=cancel_event,
    )


def stream(
    base_url: str,
    *,
    prompt: str,
    system: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    response_format: dict[str, Any] | None = None,
    timeout: float = 600.0,
    cancel_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    body: dict[str, Any] = {"prompt": prompt}
    normalized_response_format = _normalize_response_format(response_format)
    if system is not None:
        body["system"] = system
    if provider is not None:
        body["provider"] = provider
    if model is not None:
        body["model"] = model
    if normalized_response_format is not None:
        body["responseFormat"] = normalized_response_format
    generation: dict[str, Any] = {}
    if max_tokens is not None:
        generation["maxTokens"] = max_tokens
    if temperature is not None:
        generation["temperature"] = temperature
    if top_p is not None:
        generation["topP"] = top_p
    if generation:
        body["generation"] = generation
    yield from _terminal_sse_request(
        base_url,
        "POST",
        "/stream",
        body=body,
        timeout=timeout,
        cancel_event=cancel_event,
    )


def agent_start(
    base_url: str,
    *,
    prompt: str,
    system: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    generation: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    timeout: float = 600.0,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"prompt": prompt, "tools": tools or []}
    normalized_response_format = _normalize_response_format(response_format)
    if system is not None:
        body["system"] = system
    if provider is not None:
        body["provider"] = provider
    if model is not None:
        body["model"] = model
    if normalized_response_format is not None:
        body["responseFormat"] = normalized_response_format
    if generation:
        body["generation"] = generation
    return _request(base_url, "POST", "/agent/start", body=body, timeout=timeout, cancel_event=cancel_event)


def agent_events(
    base_url: str,
    session_id: str,
    *,
    timeout: float = 600.0,
    cancel_event: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    yield from _terminal_sse_request(
        base_url,
        "GET",
        f"/agent/{quote(session_id)}/events",
        timeout=timeout,
        cancel_event=cancel_event,
    )


def agent_tool_result(
    base_url: str,
    session_id: str,
    *,
    call_id: str,
    result: Any,
    is_error: bool = False,
    timeout: float = 600.0,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    body = {"call_id": call_id, "result": result, "is_error": is_error}
    return _request(
        base_url,
        "POST",
        f"/agent/{quote(session_id)}/tool_result",
        body=body,
        timeout=timeout,
        cancel_event=cancel_event,
    )


def agent_stop(base_url: str, session_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
    return _request(base_url, "DELETE", f"/agent/{quote(session_id)}", timeout=timeout)


def oauth_providers(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    return _request(base_url, "GET", "/oauth/providers", timeout=timeout)


def oauth_start(
    base_url: str,
    *,
    provider: str,
    method: str = "browser",
    enterprise_domain: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {"provider": provider, "method": method}
    if enterprise_domain:
        body["enterpriseDomain"] = enterprise_domain
    return _request(base_url, "POST", "/oauth/start", body=body, timeout=timeout)


def oauth_poll(base_url: str, provider: str, poll_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
    return _request(base_url, "GET", f"/oauth/poll/{quote(provider)}/{quote(poll_id)}", timeout=timeout)


def oauth_refresh(base_url: str, provider: str, *, timeout: float = 15.0) -> dict[str, Any]:
    return _request(base_url, "POST", f"/oauth/refresh/{quote(provider)}", timeout=timeout)
