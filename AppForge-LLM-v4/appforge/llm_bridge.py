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

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote


class BridgeError(RuntimeError):
    """Raised when the bridge is unreachable or returns an error payload."""

    def __init__(self, message: str, *, status_code: int = 0, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    body: Any | None = None,
    timeout: float = 30.0,
) -> Any:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost bridge
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover - best effort
            pass
        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(detail) if detail else None
        except json.JSONDecodeError:
            parsed = None
        message = (parsed or {}).get("error", {}).get("message") if parsed else detail
        raise BridgeError(message or f"bridge returned HTTP {exc.code}", status_code=exc.code, payload=parsed) from exc
    except urllib.error.URLError as exc:
        raise BridgeError(f"LLM 브릿지에 연결할 수 없습니다: {exc.reason}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError("LLM 브릿지 응답이 올바른 JSON이 아닙니다.") from exc


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
    timeout: float = 600.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {"prompt": prompt}
    if system is not None:
        body["system"] = system
    if provider is not None:
        body["provider"] = provider
    if model is not None:
        body["model"] = model
    generation: dict[str, Any] = {}
    if max_tokens is not None:
        generation["maxTokens"] = max_tokens
    if temperature is not None:
        generation["temperature"] = temperature
    if generation:
        body["generation"] = generation
    return _request(base_url, "POST", "/generate", body=body, timeout=timeout)
