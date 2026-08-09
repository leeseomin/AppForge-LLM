from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import llm_bridge
from .util import command_exists

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
BRIDGE_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    "APPFORGE_LLM_CONFIG_DIR",
    "APPFORGE_LLM_CONFIG",
    "APPFORGE_LLM_SECRET_BACKEND",
    "APPFORGE_MODELS_DEV_CACHE",
    "APPFORGE_MODELS_DEV_URL",
    "APPFORGE_LLM_BRIDGE_HEARTBEAT_MS",
    "APPFORGE_LLM_BRIDGE_IDLE_TIMEOUT",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "BASETEN_API_KEY",
}


def _bridge_environment(bind_host: str, bind_port: str, auth_token: str) -> dict[str, str]:
    """Build the least-privilege environment for the trusted bridge process."""

    env = {key: value for key, value in os.environ.items() if key in BRIDGE_ENV_KEYS}
    env["APPFORGE_LLM_BRIDGE_HOST"] = bind_host
    env["APPFORGE_LLM_BRIDGE_PORT"] = bind_port
    env["APPFORGE_LLM_BRIDGE_TOKEN"] = auth_token
    return env


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(name: str, default: float, *, minimum: float = 0.1) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(parsed, minimum)


def _bridge_bind(base_url: str) -> tuple[str, str]:
    parsed = urlparse(base_url)
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise llm_bridge.BridgeError(
            "로컬 LLM 브릿지 자동 시작 URL 형식이 올바르지 않습니다.",
            payload={
                "action": "APPFORGE_LLM_BRIDGE_URL을 http://127.0.0.1:8788 형식으로 설정하세요.",
                "reason": "invalid_local_bridge_url",
            },
        )
    if parsed.scheme != "http":
        raise llm_bridge.BridgeError(
            "로컬 LLM 브릿지는 http URL에서만 자동 시작할 수 있습니다.",
            payload={
                "action": "APPFORGE_LLM_BRIDGE_URL을 http://127.0.0.1:8788 형식으로 설정하거나 브릿지를 직접 실행하세요.",
                "reason": "unsupported_scheme",
            },
        )
    host = parsed.hostname or ""
    if host not in LOOPBACK_HOSTS:
        raise llm_bridge.BridgeError(
            "원격 LLM 브릿지 URL은 자동 시작할 수 없습니다.",
            payload={
                "action": "원격 브릿지 서버를 직접 실행한 뒤 APPFORGE_LLM_BRIDGE_URL이 올바른지 확인하세요.",
                "reason": "non_loopback_url",
            },
        )
    port = parsed.port or 8788
    return ("127.0.0.1" if host == "localhost" else host, str(port))


class LLMBridgeProcessManager:
    """Starts the bundled Bun bridge on demand for local settings/API calls."""

    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        runtime_dir: Path | None = None,
        enabled: bool | None = None,
        timeout: float | None = None,
    ) -> None:
        self.root_dir = (root_dir or Path(__file__).resolve().parents[1]).resolve()
        raw_runtime = runtime_dir or Path(".appforge-web")
        self.runtime_dir = raw_runtime if raw_runtime.is_absolute() else self.root_dir / raw_runtime
        self.enabled = (
            enabled
            if enabled is not None
            else _env_bool("APPFORGE_LLM_BRIDGE_AUTOSTART", True)
            and not _env_bool("APPFORGE_SKIP_LLM_BRIDGE", False)
        )
        self.timeout = timeout if timeout is not None else _env_float("APPFORGE_BRIDGE_TIMEOUT", 15.0)
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self.auth_token = os.environ.get("APPFORGE_LLM_BRIDGE_TOKEN") or secrets.token_urlsafe(48)
        self._registered_urls: set[str] = set()

    def ensure_running(
        self,
        base_url: str,
        initial_error: llm_bridge.BridgeError | None = None,
    ) -> None:
        if len(self.auth_token) < 32:
            raise llm_bridge.BridgeError(
                "LLM 브릿지 인증 token은 32자 이상이어야 합니다.",
                payload={"error": {"code": "BRIDGE_AUTH_TOKEN_WEAK"}},
            )
        llm_bridge.register_bridge_token(base_url, self.auth_token)
        self._registered_urls.add(base_url)
        with self._lock:
            self._forget_exited_process()
            if self._is_healthy(base_url):
                return
            if not self.enabled:
                llm_bridge.unregister_bridge_token(base_url, self.auth_token)
                self._registered_urls.discard(base_url)
                raise llm_bridge.BridgeError(
                    "LLM 브릿지 자동 시작이 비활성화되어 있습니다.",
                    payload={
                        "action": "APPFORGE_LLM_BRIDGE_AUTOSTART=1로 실행하거나 동일한 APPFORGE_LLM_BRIDGE_TOKEN으로 브릿지를 직접 실행하세요.",
                        "reason": "autostart_disabled",
                        "initial_error": str(initial_error) if initial_error else None,
                    },
                )

            process = self._process or self._start_process(base_url)
            deadline = time.monotonic() + self.timeout
            last_error: llm_bridge.BridgeError | None = initial_error
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise self._startup_error(
                        "LLM 브릿지가 시작 직후 종료되었습니다.",
                        "process_exited",
                        initial_error,
                    )
                try:
                    llm_bridge.ping(base_url, timeout=1.0)
                    llm_bridge.ready(base_url, timeout=1.0)
                    return
                except llm_bridge.BridgeError as exc:
                    last_error = exc
                    time.sleep(0.2)

            raise self._startup_error(
                f"LLM 브릿지가 {self.timeout:g}초 안에 준비되지 않았습니다.",
                "startup_timeout",
                last_error or initial_error,
            )

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if not process or process.poll() is not None:
            process = None
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for base_url in self._registered_urls:
            llm_bridge.unregister_bridge_token(base_url, self.auth_token)
        self._registered_urls.clear()

    def _is_healthy(self, base_url: str) -> bool:
        try:
            llm_bridge.ping(base_url, timeout=1.0)
            llm_bridge.ready(base_url, timeout=1.0)
            return True
        except llm_bridge.BridgeError as exc:
            if exc.status_code in {401, 403}:
                raise llm_bridge.BridgeError(
                    "실행 중인 LLM 브릿지와 인증 token이 일치하지 않습니다.",
                    status_code=exc.status_code,
                    payload={"error": {"code": "BRIDGE_AUTH_MISMATCH"}},
                ) from exc
            return False

    def _start_process(self, base_url: str) -> subprocess.Popen[bytes]:
        bind_host, bind_port = _bridge_bind(base_url)
        bridge_dir = self.root_dir / "llm_bridge"
        if not (bridge_dir / "src" / "index.ts").is_file():
            raise llm_bridge.BridgeError(
                "번들 LLM 브릿지 소스를 찾을 수 없습니다.",
                payload={
                    "action": "AppForge-LLM-v6 소스 루트에서 실행 중인지 확인하세요.",
                    "reason": "bridge_source_missing",
                    "bridge_dir": str(bridge_dir),
                },
            )
        if not command_exists("bun"):
            raise llm_bridge.BridgeError(
                "Bun을 찾을 수 없어 LLM 브릿지를 자동 시작할 수 없습니다.",
                payload={
                    "action": "Bun을 설치하거나 llm_bridge 서비스를 직접 실행한 뒤 다시 시도하세요.",
                    "reason": "bun_missing",
                },
            )
        if not (bridge_dir / "node_modules").is_dir():
            raise llm_bridge.BridgeError(
                "llm_bridge 의존성이 설치되어 있지 않습니다.",
                payload={
                    "action": "llm_bridge 폴더에서 bun install을 실행한 뒤 다시 LLM 연결 설정을 여세요.",
                    "reason": "node_modules_missing",
                    "bridge_dir": str(bridge_dir),
                },
            )

        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.runtime_dir.chmod(0o700)
        log_path = self.runtime_dir / "llm-bridge.log"
        env = _bridge_environment(bind_host, bind_port, self.auth_token)
        with log_path.open("ab") as log:
            if os.name != "nt":
                log_path.chmod(0o600)
            log.write(b"\n--- appforge web autostart ---\n")
            process = subprocess.Popen(
                ["bun", "run", "start"],
                cwd=str(bridge_dir),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self._process = process
        return process

    def _forget_exited_process(self) -> None:
        if self._process and self._process.poll() is not None:
            self._process = None

    def _startup_error(
        self,
        message: str,
        reason: str,
        error: llm_bridge.BridgeError | None,
    ) -> llm_bridge.BridgeError:
        payload: dict[str, Any] = {
            "action": "llm_bridge 로그를 확인한 뒤 LLM 연결 설정을 다시 여세요.",
            "reason": reason,
            "log_path": str(self.runtime_dir / "llm-bridge.log"),
        }
        if error:
            payload["initial_error"] = str(error)
            if error.payload:
                payload["initial_payload"] = error.payload
        return llm_bridge.BridgeError(message, payload=payload)
