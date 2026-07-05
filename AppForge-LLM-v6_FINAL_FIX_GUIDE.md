# AppForge-LLM v6 최종 수정 가이드

작성일: 2026-07-05  
대상: `AppForge-LLM v6` Python CLI/FastAPI 웹앱 + Vue 프론트엔드 + Node LLM 브리지  
목적: 로컬 웹 UI의 보안 경계, 에이전트 도구 실행 정책, SSE 안정성, 오류 노출, 설정 정합성을 릴리스 가능한 수준으로 보강한다.

---

## 1. 결론 요약

아래 순서대로 수정한다.

| 우선순위 | 수정 항목 | 핵심 목표 |
|---|---|---|
| **P0** | 로컬 웹서버 CSRF/DNS rebinding 방어 | 로컬 앱이 악성 웹페이지에서 임의 조작/종료되지 않게 한다. |
| **P0** | LLM tool safety flag 서버 강제 적용 | 모델이 `allow_destructive`/`allow_network` 값을 직접 올려 정책을 우회하지 못하게 한다. |
| **P0** | 명령 실행 정책 강화 | denylist만으로 shell/interpreter 우회를 막는 구조를 allowlist 또는 sandbox 기반으로 바꾼다. |
| **P1** | SSE thread leak + keepalive | 클라이언트 연결 종료 시 threadpool worker가 무기한 대기하지 않게 한다. |
| **P1** | SSE event id/replay/gap 처리 | 긴 작업 중 재연결 또는 큐 포화 시 이벤트 유실을 명확히 감지하고 상태를 복구한다. |
| **P1** | 오류 응답 내부정보 제거 | traceback, 경로, 내부 exception 문자열은 로그/내부 상태에만 남기고 API 응답에서는 숨긴다. |
| **P2** | `WebConfig.llm_router` 기본값 정합성 | dataclass 직접 생성과 env 생성 경로의 동작을 일치시킨다. |
| **P2** | preview origin 격리/CSP 강화 | 생성 앱 preview가 AppForge API와 같은 origin 권한을 갖지 않게 한다. |
| **P2** | API key 저장소 개선 | chmod 0600 평문 JSON을 유지하되 OS keychain 선택지를 추가한다. |
| **P3** | web build 산출물 관리 정책 | 저장소 커밋/패키지 포함/CI 검증 정책을 명확히 한다. |

---

## 2. 권장 작업 단위

한 번에 모두 고치기보다 아래처럼 PR 또는 commit을 나누는 것을 권장한다.

1. `web-security-local-guard`: Host/Origin/token guard, session 종료 보호, 프론트 API token 전송.
2. `tool-safety-enforcement`: LLM tool schema에서 정책 flag 제거, driver에서 서버 정책 강제 주입, destructive/network 우회 테스트.
3. `command-policy-hardening`: shell/interpreter 우회 차단 또는 sandbox runner 도입.
4. `sse-resilience`: timeout get, keepalive, event id, replay, gap 이벤트, 프론트 reconnect 처리.
5. `error-redaction`: 전역 예외 핸들러와 public job payload sanitize.
6. `config-preview-storage-cleanup`: `llm_router` 기본값, preview 격리, keychain 옵션, web bundle 관리.

---

## 3. P0-1. 로컬 웹서버 CSRF/DNS rebinding 방어

### 3.1 현재 문제

대상 파일:

- `appforge/web.py`
- `frontend/src/api.ts`
- `frontend/src/App.vue`

현재 FastAPI 서버는 기본적으로 `127.0.0.1`에 바인딩되지만, 다음 방어가 없다.

- Host header whitelist 없음
- Origin/Referer 검증 없음
- 세션별 random token 없음
- `POST /api/session/end`가 body 없이 호출 가능
- 악성 웹페이지가 form POST로 로컬 서버 종료를 유도할 수 있음

특히 `POST /api/session/end`는 세션 종료와 프로세스 종료 예약을 수행하므로 우선 보호해야 한다.

### 3.2 목표 동작

1. 서버 시작 시 `secrets.token_urlsafe(32)`로 세션 token을 생성한다.
2. 자동 브라우저 오픈 URL에 token을 1회성 bootstrap query로 붙인다.
3. 프론트는 URL에서 token을 읽어 `sessionStorage`에 저장하고 URL에서는 제거한다.
4. 모든 mutating API 요청에는 `X-AppForge-Token` header를 붙인다.
5. SSE는 `EventSource`가 custom header를 지원하지 않으므로 query token을 허용한다.
6. 서버는 다음을 검증한다.
   - Host가 loopback whitelist에 포함되는지
   - unsafe method의 Origin/Referer가 local AppForge origin인지
   - 보호 대상 API 요청에 token이 있는지
7. `/preview/`는 token을 절대 주입하지 않는다.

### 3.3 서버 수정 가이드

`appforge/web.py`에 다음 import를 추가한다.

```python
import logging
import secrets
from urllib.parse import urlparse
```

상단에 logger와 helper를 추가한다.

```python
logger = logging.getLogger(__name__)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _host_without_port(value: str) -> str:
    host = value.split(",", 1)[0].strip()
    if host.startswith("["):
        return host.split("]", 1)[0].strip("[]")
    return host.split(":", 1)[0]


def _is_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    return _host_without_port(value).casefold() in LOCAL_HOSTS


def _origin_host(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urlparse(value).hostname
    except Exception:
        return None
```

`create_app()`에서 token을 만든 뒤 `app.state`에 저장한다.

```python
def create_app(..., session_token: str | None = None) -> FastAPI:
    resolved_config = config or WebConfig.from_env()
    resolved_session_token = session_token or secrets.token_urlsafe(32)
    ...
    app.state.session_token = resolved_session_token
```

기존 `security_headers` middleware를 “요청 검증 + 보안 헤더”로 확장한다. 핵심은 `call_next()` 전에 Host/Origin/token을 검사하는 것이다.

```python
@app.middleware("http")
async def local_request_guard(request: Request, call_next: Any) -> Any:
    path = request.url.path

    # 1. DNS rebinding 기본 방어
    host = request.headers.get("host")
    if not _is_loopback_host(host):
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "FORBIDDEN_HOST", "message": "허용되지 않은 Host header입니다."}},
        )

    # 2. Cross-site form/fetch 방어
    if request.method.upper() not in SAFE_METHODS:
        origin_host = _origin_host(request.headers.get("origin"))
        referer_host = _origin_host(request.headers.get("referer"))
        if origin_host and origin_host.casefold() not in LOCAL_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "FORBIDDEN_ORIGIN", "message": "허용되지 않은 Origin입니다."}},
            )
        if not origin_host and referer_host and referer_host.casefold() not in LOCAL_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "FORBIDDEN_REFERER", "message": "허용되지 않은 Referer입니다."}},
            )

    # 3. 보호 API token 검증
    # health 같은 읽기 endpoint는 필요에 따라 예외 처리할 수 있으나, mutating endpoint는 반드시 요구한다.
    protected = path.startswith("/api/") and path not in {"/api/health"}
    if protected:
        expected = str(request.app.state.session_token)
        supplied = request.headers.get("x-appforge-token")
        if path.endswith("/events"):
            supplied = supplied or request.query_params.get("token")
        if not supplied or not secrets.compare_digest(supplied, expected):
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "INVALID_SESSION_TOKEN", "message": "세션 token이 없거나 올바르지 않습니다."}},
            )

    response = await call_next(request)
    ...  # 기존 security headers 유지
    return response
```

`serve()`에서 browser open URL에 token을 넣는다.

```python
def serve(...):
    config = WebConfig.from_env()
    token = secrets.token_urlsafe(32)
    app = create_app(config, session_token=token)
    if open_browser:
        url = f"http://{host}:{port}/?token={token}"
        ...
```

`--no-browser` 모드에서는 token이 없으면 웹 UI 사용이 어려우므로 서버 시작 시 token URL을 로그로 남긴다.

```python
logger.info("Open AppForge Web UI: http://%s:%s/?token=%s", host, port, token)
```

> 주의: token을 cookie로 자동 전송하는 방식만 쓰면 `/preview/`가 same-origin일 때 생성 앱 script가 API를 호출할 수 있다. 그래서 기본 방어는 `X-AppForge-Token` header 기반으로 두고, preview에는 token을 주입하지 않는다.

### 3.4 프론트 수정 가이드

`frontend/src/api.ts`에 token helper를 추가한다.

```ts
const TOKEN_KEY = 'appforge.sessionToken';

export function bootstrapSessionToken(): void {
  const url = new URL(window.location.href);
  const token = url.searchParams.get('token');
  if (!token) return;
  sessionStorage.setItem(TOKEN_KEY, token);
  url.searchParams.delete('token');
  window.history.replaceState({}, document.title, url.toString());
}

export function getSessionToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}
```

`request()`에 header를 붙인다.

```ts
const token = getSessionToken();
const response = await fetch(path, {
  ...options,
  headers: {
    Accept: 'application/json',
    ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    ...(token ? { 'X-AppForge-Token': token } : {}),
    ...(options.headers || {}),
  },
});
```

`frontend/src/App.vue` 초기화 지점에서 bootstrap을 호출한다.

```ts
import { bootstrapSessionToken, getSessionToken } from './api';

bootstrapSessionToken();
```

SSE URL에는 token query를 붙인다.

```ts
const url = new URL(`/api/jobs/${encodeURIComponent(jobId)}/events`, window.location.origin);
const token = getSessionToken();
if (token) url.searchParams.set('token', token);
eventSource = new EventSource(url.toString());
```

### 3.5 테스트 추가

`tests/test_web.py`에 아래 성격의 테스트를 추가한다.

```python
def test_mutating_api_requires_session_token(tmp_path):
    app = create_app(_fixture_config(tmp_path), session_token="test-token", llm_bridge_manager=_FakeBridgeManager())
    client = TestClient(app)

    response = client.post("/api/session/end")
    assert response.status_code == 403


def test_mutating_api_accepts_valid_session_token(tmp_path):
    called = False

    def shutdown_callback():
        nonlocal called
        called = True

    app = create_app(
        _fixture_config(tmp_path),
        session_token="test-token",
        llm_bridge_manager=_FakeBridgeManager(),
        shutdown_callback=shutdown_callback,
    )
    client = TestClient(app)

    response = client.post("/api/session/end", headers={"X-AppForge-Token": "test-token"})
    assert response.status_code == 200
```

Host/Origin 방어 테스트도 추가한다.

```python
def test_rejects_non_loopback_host(tmp_path):
    app = create_app(_fixture_config(tmp_path), session_token="test-token", llm_bridge_manager=_FakeBridgeManager())
    client = TestClient(app)

    response = client.get("/api/health", headers={"host": "attacker.test"})
    assert response.status_code == 403


def test_rejects_cross_origin_post(tmp_path):
    app = create_app(_fixture_config(tmp_path), session_token="test-token", llm_bridge_manager=_FakeBridgeManager())
    client = TestClient(app)

    response = client.post(
        "/api/session/end",
        headers={"host": "127.0.0.1:8787", "origin": "https://evil.example", "X-AppForge-Token": "test-token"},
    )
    assert response.status_code == 403
```

---

## 4. P0-2. LLM tool safety flag 서버 강제 적용

### 4.1 현재 문제

대상 파일:

- `appforge/drivers.py`
- `appforge/tooling/tools/execution.py`
- `appforge/tooling/base.py`

현재 `drivers.py`의 `_execute_registered_tool()`은 정책값을 `setdefault()`로만 넣는다.

```python
payload.setdefault("allow_network", bool(safety.get("allow_network", False)))
payload.setdefault("allow_destructive", bool(safety.get("allow_destructive", False)))
```

즉 LLM이 tool call 인자에 `allow_destructive: true` 또는 `allow_network: true`를 직접 넣으면 서버 정책이 `False`여도 payload 값이 유지될 수 있다.

### 4.2 필수 수정

`appforge/drivers.py`에서 `setdefault()`를 “무조건 덮어쓰기”로 바꾼다.

```python
safety = project.get("safety") if isinstance(project.get("safety"), dict) else {}
payload = dict(arguments)

if getattr(tool, "network_required", False):
    payload["allow_network"] = bool(safety.get("allow_network", False))
else:
    payload.pop("allow_network", None)

if getattr(tool, "destructive", False):
    payload["allow_destructive"] = bool(safety.get("allow_destructive", False))
else:
    payload.pop("allow_destructive", None)

payload["timeout"] = min(int(payload.get("timeout", self.max_tool_seconds)), self.max_tool_seconds, 120)
return tool.run(layout.root, payload)
```

정책은 모델 입력이 아니라 서버 결정값이어야 한다.

### 4.3 LLM-visible schema에서 정책 flag 제거

`RunCommandTool.input_schema`는 내부 검증용으로 유지할 수 있지만, LLM에 보여주는 schema에서는 `allow_network`, `allow_destructive`를 제거한다.

`appforge/tooling/tools/execution.py`:

```python
class RunCommandTool(Tool):
    ...
    input_schema = {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "timeout": {"type": "integer"},
            "allow_network": {"type": "boolean"},       # internal only
            "allow_destructive": {"type": "boolean"},  # internal only
            "env": {"type": "object"},
        },
    }
    llm_parameters = {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "timeout": {"type": "integer"},
            "env": {"type": "object"},
        },
    }
```

다른 tool도 동일한 원칙을 따른다.

- `allow_*`는 internal payload로만 존재한다.
- LLM prompt/tool schema에는 노출하지 않는다.
- 서버가 project safety를 기준으로 강제로 주입한다.

### 4.4 `InstallDependenciesTool` 보정

현재 `InstallDependenciesTool.execute()`는 `allow_destructive=True`를 고정으로 넣는다. Base `Tool.run()`에서 destructive 검사가 먼저 수행되기는 하지만, 일관성을 위해 내부 command policy도 입력값을 따른다.

```python
return run_command(
    workspace,
    command,
    policy=CommandPolicy(
        allow_network=bool(inputs.get("allow_network", False)),
        allow_destructive=bool(inputs.get("allow_destructive", False)),
        timeout_seconds=int(inputs.get("timeout", 1200)),
    ),
)
```

### 4.5 테스트 추가

`tests/test_tools.py` 또는 driver 전용 테스트에 추가한다.

```python
def test_llm_tool_arguments_cannot_escalate_safety_flags(tmp_path):
    # project safety가 false일 때 LLM이 allow_destructive=true를 넣어도 driver가 false로 덮어써야 한다.
    # mock registry/tool 또는 최소 AgentDriver._execute_registered_tool 호출로 검증한다.
    ...
```

필수 assertion:

- `project["safety"]["allow_destructive"] == False`
- arguments에 `allow_destructive=True`를 넣음
- 실제 tool 실행 payload는 `allow_destructive=False`
- destructive tool은 실패해야 함

---

## 5. P0-3. 명령 실행 정책 강화

### 5.1 현재 문제

대상 파일:

- `appforge/tooling/command.py`
- `appforge/tooling/tools/execution.py`
- `appforge/tooling/tools/quality.py`

현재 명령 검증은 denylist 정규식 기반이다. 다음 유형의 우회를 막기 어렵다.

```bash
bash -lc 'rm -rf .'
python -c 'import shutil; shutil.rmtree(".")'
node -e 'require("fs").rmSync(".", {recursive:true, force:true})'
```

`subprocess.run(..., shell=False)`는 shell injection 방어에는 도움이 되지만, shell/interpreter 자체를 직접 실행하는 경우에는 안전 경계가 되지 않는다.

### 5.2 최소 패치

`allow_destructive=False`일 때 다음을 차단한다.

1. shell 실행기
   - `sh`, `bash`, `zsh`, `fish`, `cmd`, `powershell`, `pwsh`
2. interpreter inline code
   - `python -c`, `python -m pip install`, `node -e`, `ruby -e`, `perl -e`
3. 절대경로 destructive target
   - `/`, `$HOME`, `%USERPROFILE%`, `/tmp` 전체 삭제 등
4. repository destructive operation
   - `git reset --hard`, `git clean -fd`, force push

예시:

```python
_SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}
_INLINE_CODE_FLAGS = {"-c", "-e"}
_INTERPRETERS = {"python", "python3", "node", "ruby", "perl"}


def validate_command(argv: list[str], policy: CommandPolicy) -> None:
    executable = Path(argv[0]).name.lower()
    rendered = shlex.join(argv)

    if not policy.allow_destructive:
        if executable in _SHELL_EXECUTABLES:
            raise PermissionError("Shell execution requires allow_destructive=true or sandbox isolation")
        if executable in _INTERPRETERS and any(arg in _INLINE_CODE_FLAGS for arg in argv[1:3]):
            raise PermissionError("Inline interpreter execution requires allow_destructive=true or sandbox isolation")
        for pattern in _BLOCKED_PATTERNS:
            if pattern.search(rendered):
                raise PermissionError(f"Blocked potentially destructive command: {rendered}")

    ...  # 기존 network 검증
```

기존 테스트 중 `python -c`를 사용하는 테스트는 temp script 파일 실행 방식으로 바꾼다.

```python
script = tmp_path / "print_env.py"
script.write_text("import os\nprint(os.environ.get('APPFORGE_TEST_SECRET', 'missing'))\n", encoding="utf-8")
result = run_command(tmp_path, [sys.executable, str(script)], env={...})
```

### 5.3 권장 패치: workspace sandbox

장기적으로는 allowlist/denylist만으로 충분하지 않다. 명령 실행은 아래 중 하나로 격리한다.

#### 옵션 A. 컨테이너 실행

- workspace를 container 내부 `/workspace`에 bind mount
- 기본 network off
- `allow_network=True`일 때만 network 허용
- host home, SSH key, credential file mount 금지
- memory/process/time limit 적용
- rootless container 우선 사용

#### 옵션 B. OS sandbox

- macOS: sandbox-exec 대체가 제한적이므로 별도 runner 필요
- Linux: bubblewrap/firejail/nsjail 중 하나 검토
- Windows: Job Object/AppContainer 또는 WSL container 검토

#### 옵션 C. 제한된 allowlist

컨테이너 도입 전까지는 `allow_destructive=False`에서 다음처럼 제한한다.

```python
NON_DESTRUCTIVE_ALLOWLIST = {
    "git": {"status", "diff", "log", "show", "rev-parse"},
    "python": {"-m pytest", "-m build"},
    "npm": {"test", "run lint", "run typecheck", "run build"},
    "pnpm": {"test", "run lint", "run typecheck", "run build"},
    "yarn": {"test", "lint", "typecheck", "build"},
}
```

단, `npm run build`도 package script를 실행하므로 완전한 보안 경계는 아니다. 신뢰할 수 없는 생성 코드에 대해서는 sandbox를 최종 목표로 둔다.

### 5.4 테스트 추가

```python
def test_command_policy_blocks_shell_escape_when_destructive_disabled(tmp_path):
    with pytest.raises(PermissionError):
        run_command(tmp_path, ["bash", "-lc", "echo ok"], policy=CommandPolicy())


def test_command_policy_blocks_inline_python_when_destructive_disabled(tmp_path):
    with pytest.raises(PermissionError):
        run_command(tmp_path, [sys.executable, "-c", "print('ok')"], policy=CommandPolicy())
```

---

## 6. P1-1. SSE thread leak 수정

### 6.1 현재 문제

대상 파일:

- `appforge/web.py`
- `appforge/web_jobs.py`

현재 `/api/jobs/{job_id}/events`는 다음 형태로 동작한다.

```python
item = await run_in_threadpool(subscriber.get)
```

`queue.Queue.get()`에 timeout이 없기 때문에 클라이언트가 연결을 끊어도 다음 이벤트가 들어오기 전까지 threadpool worker가 대기할 수 있다.

### 6.2 수정 가이드

`job_events()`에 `Request`를 추가하고, `queue.Empty`를 처리한다.

```python
import queue
```

```python
@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    subscriber = resolved_manager.subscribe_events(job_id)

    async def event_stream() -> AsyncIterator[str]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await run_in_threadpool(subscriber.get, True, 15.0)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue

                event_name = str(item.get("event") or "message")
                event_id = item.get("id")
                lines = []
                if event_id is not None:
                    lines.append(f"id: {event_id}")
                lines.append(f"event: {event_name}")
                lines.append(f"data: {json.dumps(item, ensure_ascii=False)}")
                yield "\n".join(lines) + "\n\n"

                if event_name in {"job_completed", "job_failed", "job_cancelled"}:
                    break
        finally:
            resolved_manager.unsubscribe_events(job_id, subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
```

### 6.3 acceptance criteria

- SSE 연결 후 브라우저 탭을 닫아도 threadpool worker가 계속 쌓이지 않는다.
- 이벤트가 없어도 15초마다 keepalive comment가 전송된다.
- terminal event 이후 stream이 정상 종료된다.

---

## 7. P1-2. SSE event id, replay, gap 처리

### 7.1 현재 문제

대상 파일:

- `appforge/web_jobs.py`
- `appforge/web.py`
- `frontend/src/App.vue`

현재 job event는 `MAX_EVENTS=160` 링버퍼에 저장되지만 다음이 없다.

- event id
- `Last-Event-ID` 기반 replay
- queue full 시 gap 표시
- 프론트 gap 감지 후 resync 처리

### 7.2 서버 event id 추가

`_record_event_locked()`에서 monotonically increasing id를 부여한다.

```python
def _record_event_locked(...):
    events = job.setdefault("events", [])
    event_id = int(job.get("next_event_id") or 1)
    job["next_event_id"] = event_id + 1
    record = {
        "id": event_id,
        "event": event,
        "message": _clean_text(message, 1_000),
        "timestamp": utc_now(),
        "data": _sanitize_value(data or {}, max_text=1_000),
    }
    ...
```

기존 job JSON에는 `next_event_id`가 없을 수 있으므로 `_load_jobs()` 또는 `_record_event_locked()`에서 fallback 처리한다.

```python
if "next_event_id" not in job:
    max_id = max((int(e.get("id") or 0) for e in job.get("events") or []), default=0)
    job["next_event_id"] = max_id + 1
```

### 7.3 replay 지원

`subscribe_events()` signature를 확장한다.

```python
def subscribe_events(self, job_id: str, *, last_event_id: int | None = None) -> queue.Queue[dict[str, Any]]:
    with self._lock:
        job = self._jobs.get(job_id)
        if job is None:
            raise WebJobError("JOB_NOT_FOUND", "작업을 찾을 수 없습니다.", status_code=404)
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=200)
        self._event_subscribers.setdefault(job_id, []).append(q)

        if last_event_id is not None:
            replayed = False
            for event in job.get("events") or []:
                if int(event.get("id") or 0) > last_event_id:
                    self._put_subscriber_event(q, event)
                    replayed = True
            if not replayed:
                self._put_subscriber_event(q, {"event": "snapshot", "message": "현재 작업 상태", "timestamp": utc_now(), "job": self._public_job_locked(job)})
        else:
            self._put_subscriber_event(q, {"event": "snapshot", "message": "현재 작업 상태", "timestamp": utc_now(), "job": self._public_job_locked(job)})
        return q
```

`web.py`에서 header와 query를 모두 받는다.

```python
last_id_raw = request.headers.get("last-event-id") or request.query_params.get("lastEventId")
last_event_id = int(last_id_raw) if last_id_raw and last_id_raw.isdigit() else None
subscriber = resolved_manager.subscribe_events(job_id, last_event_id=last_event_id)
```

### 7.4 queue full gap 처리

`queue.Full`을 조용히 무시하지 말고 gap 이벤트를 보낸다.

```python
def _put_subscriber_event(self, subscriber: queue.Queue[dict[str, Any]], record: dict[str, Any]) -> None:
    try:
        subscriber.put_nowait(record)
        return
    except queue.Full:
        pass

    try:
        subscriber.get_nowait()  # 가장 오래된 이벤트 하나 제거
    except queue.Empty:
        pass

    gap = {
        "id": record.get("id"),
        "event": "event_gap",
        "message": "일부 이벤트가 유실되어 현재 상태를 다시 불러와야 합니다.",
        "timestamp": utc_now(),
        "data": {"reason": "subscriber_queue_full"},
    }
    try:
        subscriber.put_nowait(gap)
    except queue.Full:
        pass
```

`_record_event_locked()`에서는 helper를 사용한다.

```python
for subscriber in list(self._event_subscribers.get(str(job.get("id")), [])):
    self._put_subscriber_event(subscriber, record)
```

### 7.5 프론트 reconnect 처리

`frontend/src/App.vue`에서 마지막 event id를 저장한다.

```ts
const lastEventIds = new Map<string, string>();
```

SSE URL 생성 시 붙인다.

```ts
const lastEventId = lastEventIds.get(jobId);
if (lastEventId) url.searchParams.set('lastEventId', lastEventId);
```

event handler에서 업데이트한다.

```ts
const refreshFromEvent = (event: MessageEvent) => {
  if (event.lastEventId) {
    lastEventIds.set(jobId, event.lastEventId);
  }
  const payload = JSON.parse(event.data || '{}');
  if (payload.event === 'event_gap') {
    loadCurrentJob({ immediate: true });
    return;
  }
  ...
};
```

이벤트 목록에 `event_gap`을 추가한다.

```ts
'event_gap',
```

### 7.6 acceptance criteria

- 재연결 시 `lastEventId` 이후 이벤트가 replay된다.
- replay할 수 없는 경우 snapshot으로 복구한다.
- subscriber queue가 꽉 차도 silent drop이 발생하지 않는다.
- 프론트는 `event_gap` 수신 시 즉시 `GET /api/jobs/{id}`로 상태를 다시 불러온다.

---

## 8. P1-3. 오류 응답 내부정보 제거

### 8.1 현재 문제

대상 파일:

- `appforge/web.py`
- `appforge/web_jobs.py`

현재 전역 exception handler는 exception type과 message를 그대로 API 응답에 담는다.

```python
"message": f"{type(exc).__name__}: {exc}"
```

작업 실패 상태에도 `technical.traceback` 또는 내부 경로가 public payload에 포함될 수 있다.

### 8.2 전역 exception handler 수정

`appforge/web.py`:

```python
@app.exception_handler(Exception)
async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled web error")
    error = {
        "code": "INTERNAL_SERVER_ERROR",
        "title": "서버 오류가 발생했습니다",
        "message": "요청을 처리하는 중 예기치 못한 오류가 발생했습니다.",
        "action": "서버 로그를 확인한 뒤 웹앱을 다시 시작하세요.",
        "context": {},
    }
    return JSONResponse(status_code=500, content={"error": error})
```

`RequestValidationError`의 `exc.errors()`도 입력값 일부를 포함할 수 있으므로, 외부 응답에서는 간략화한다.

```python
"context": {"fields": [".".join(map(str, item.get("loc", []))) for item in exc.errors()]}
```

### 8.3 public job payload sanitize

`appforge/web_jobs.py`의 `_public_job_locked()`에서 error technical을 제거한다.

```python
def _public_job_locked(self, job: dict[str, Any]) -> dict[str, Any]:
    public = copy.deepcopy(job)
    public.pop("archive_path", None)

    if isinstance(public.get("error"), dict):
        public["error"] = self._compact_error(public["error"])

    for stage in public.get("stages") or []:
        if isinstance(stage.get("error"), dict):
            stage["error"] = self._compact_error(stage["error"])

    for event in public.get("events") or []:
        if isinstance(event.get("data"), dict):
            event["data"].pop("traceback", None)
            event["data"].pop("exception", None)
            event["data"].pop("technical", None)

    public["progress"] = self._progress(job)
    public["terminal"] = job.get("status") in TERMINAL_JOB_STATUSES
    ...
    return public
```

내부 진단용 traceback은 job JSON 또는 로그 파일에 남길 수 있다. 단, API 응답과 UI에는 기본 노출하지 않는다.

### 8.4 테스트 추가

```python
def test_unexpected_error_response_is_generic(tmp_path):
    ...
    assert "Traceback" not in response.text
    assert "ValueError" not in response.text


def test_public_job_error_excludes_technical_traceback(tmp_path):
    ...
    payload = client.get(f"/api/jobs/{job_id}", headers={"X-AppForge-Token": "test-token"}).json()
    assert "technical" not in payload.get("error", {})
    assert "traceback" not in str(payload)
```

---

## 9. P2-1. `WebConfig.llm_router` 기본값 정합성

### 9.1 현재 문제

대상 파일:

- `appforge/web_jobs.py`
- `tests/test_web.py`

현재 dataclass 기본값과 env 생성 기본값이 다르다.

```python
llm_router: bool = False
...
llm_router=_env_bool("APPFORGE_LLM_ROUTER", True)
```

### 9.2 수정 선택지

둘 중 하나로 통일한다.

#### 권장안 A: router 기본 활성 유지

웹 UI 기본 동작이 router 사용이라면 dataclass 기본값을 `True`로 바꾼다.

```python
llm_router: bool = True
```

#### 대안 B: 보수적 기본 비활성

router를 명시적으로 켜야 하는 기능으로 둘 경우 env 기본값을 `False`로 바꾼다.

```python
llm_router=_env_bool("APPFORGE_LLM_ROUTER", False)
```

현재 `from_env()`가 `True`이므로 실제 실행 기본을 유지하려면 **권장안 A**가 덜 파괴적이다.

### 9.3 테스트 추가

```python
def test_web_config_llm_router_default_matches_env(monkeypatch):
    monkeypatch.delenv("APPFORGE_LLM_ROUTER", raising=False)
    assert WebConfig().llm_router is WebConfig.from_env().llm_router
```

---

## 10. P2-2. preview origin/CSP 강화

### 10.1 현재 상태

대상 파일:

- `appforge/web.py`
- `frontend/src/components/JobPanel.vue`

현재 iframe은 다음처럼 sandbox가 적용되어 있다.

```html
<iframe sandbox="allow-scripts" ...></iframe>
```

이 설정은 iframe 내부 preview를 opaque origin으로 만들기 때문에 좋은 완화책이다. 하지만 `/preview/...` URL을 직접 열면 AppForge API와 같은 origin에서 실행될 수 있고, preview CSP에는 `unsafe-inline`/`unsafe-eval`이 있다.

### 10.2 최소 패치

preview 응답에 CSP sandbox를 추가한다.

```python
if request.url.path.startswith("/preview/"):
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-scripts allow-forms; "
        "default-src 'self' data: blob:; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'none'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'self'"
    )
```

`unsafe-eval`은 기본 제거한다. 특정 프레임워크 preview에서 꼭 필요하면 개발 옵션으로만 허용한다.

```python
if resolved_config.preview_allow_eval:
    script_src = "script-src 'self' 'unsafe-inline' 'unsafe-eval';"
else:
    script_src = "script-src 'self' 'unsafe-inline';"
```

### 10.3 권장 패치

preview를 별도 origin으로 분리한다.

- AppForge UI/API: `http://127.0.0.1:8787`
- Preview static server: `http://127.0.0.1:8790`
- Preview server에는 `/api/*` route를 mount하지 않는다.
- preview iframe의 `src`는 별도 port URL을 사용한다.
- Preview CSP의 `connect-src`는 기본 `'none'` 또는 preview 앱에 필요한 local endpoint만 허용한다.

이 방식이 direct preview 접근까지 가장 명확하게 차단한다.

### 10.4 acceptance criteria

- preview iframe 내부에서 `fetch('/api/health')`가 실패해야 한다.
- direct `/preview/{job_id}/` 접근 시에도 AppForge API를 호출할 수 없어야 한다.
- UI의 일반 API 요청은 session token 없이는 실패해야 한다.

---

## 11. P2-3. API key 저장소 개선

### 11.1 현재 상태

대상 파일:

- `llm_bridge/src/config.ts`
- `llm_bridge/package.json`

현재 LLM provider config는 JSON 파일에 저장되고 chmod 0600이 적용된다.

```ts
await writeFile(CONFIG_PATH, payload, "utf8")
await applyPerms()
```

로컬 개발 도구로는 수용 가능한 수준이지만, API key와 OAuth credential은 가능하면 OS keychain에 저장하는 옵션을 제공하는 것이 좋다.

### 11.2 수정 방향

기본 호환성을 위해 JSON 저장은 유지하되, 선택적으로 keychain backend를 추가한다.

환경변수 예:

```bash
APPFORGE_LLM_SECRET_BACKEND=file      # 기본값
APPFORGE_LLM_SECRET_BACKEND=keychain  # 선택
```

interface 예:

```ts
interface SecretStore {
  get(providerId: string, key: 'apiKey' | 'oauth'): Promise<string | undefined>
  set(providerId: string, key: 'apiKey' | 'oauth', value: string): Promise<void>
  delete(providerId: string, key: 'apiKey' | 'oauth'): Promise<void>
}
```

JSON에는 secret 자체가 아니라 참조만 저장한다.

```json
{
  "providers": {
    "openai": {
      "apiKeyRef": "keychain:appforge/openai/apiKey",
      "baseURL": "...",
      "defaultModel": "..."
    }
  }
}
```

### 11.3 단계적 적용

1. `file` backend: 기존 동작 유지.
2. `keychain` backend: macOS Keychain, Windows Credential Manager, Linux Secret Service 지원 라이브러리 검토.
3. keychain 실패 시 명시적 오류를 반환하고 조용히 file backend로 fallback하지 않는다.
4. migration command를 추가한다.

```bash
appforge auth migrate-secrets --to keychain
```

---

## 12. P3. web build 산출물 관리 정책

### 12.1 현재 상태

대상 파일:

- `frontend/vite.config.ts`
- `pyproject.toml`
- `appforge/resources/web/**`

현재 Vite build output이 `appforge/resources/web` 아래에 들어가고, Python package data에도 포함된다. 패키지 설치만으로 웹 UI를 띄우려면 이 구조는 합리적이다.

따라서 “무조건 저장소에서 제거”보다는 아래 중 하나를 선택한다.

### 12.2 권장 정책 A: source repo에는 커밋하지 않고 wheel/sdist에만 포함

CI release job에서 다음 순서로 빌드한다.

```bash
cd frontend
npm ci
npm run build
cd ..
python -m build
```

`.gitignore`에는 다음을 추가한다.

```gitignore
appforge/resources/web/assets/index-*.js
appforge/resources/web/assets/index-*.css
```

단, source checkout 상태에서 `appforge-web`을 실행하려면 `frontend` build가 선행되어야 한다는 문서가 필요하다.

### 12.3 대안 정책 B: 커밋은 유지하되 CI에서 일치성 검증

source와 bundle drift를 막기 위해 CI에서 build 후 diff를 확인한다.

```bash
cd frontend
npm ci
npm run build
cd ..
git diff --exit-code appforge/resources/web
```

일반 사용자의 설치 편의성을 중시한다면 대안 B도 실용적이다.

---

## 13. 최종 검증 체크리스트

### 13.1 Python tests

```bash
python -m pytest tests/test_web.py tests/test_tools.py
python -m pytest
```

필수 통과 조건:

- token 없는 mutating API는 403
- 잘못된 Host/Origin은 403
- session token 있는 정상 UI 요청은 2xx
- LLM tool argument로 safety flag를 올려도 서버 정책이 우선
- shell/interpreter 우회 명령은 차단
- public job payload에 traceback/technical detail 없음
- SSE keepalive와 unsubscribe가 정상 동작

### 13.2 Frontend

```bash
cd frontend
npm ci
npm run build
```

수동 확인:

- `/?token=...`으로 접속하면 token이 `sessionStorage`에 저장되고 URL에서는 제거된다.
- 새 작업 생성/취소/재시도/종료 요청에 `X-AppForge-Token`이 붙는다.
- SSE URL에는 token query가 붙는다.
- `event_gap` 수신 시 job 상태가 다시 로드된다.

### 13.3 LLM bridge

```bash
cd llm_bridge
bun test
bun run typecheck
```

수동 확인:

- 기존 file backend는 호환된다.
- keychain backend를 켠 경우 secret이 JSON에 평문 저장되지 않는다.
- keychain 실패 시 사용자가 이해 가능한 오류가 표시된다.

### 13.4 보안 smoke test

브라우저에서 악성 페이지를 가정해 아래 form POST가 실패해야 한다.

```html
<form action="http://127.0.0.1:8787/api/session/end" method="POST">
  <button>attack</button>
</form>
```

기대 결과:

- token 없음 또는 Origin 불일치로 403
- AppForge 서버가 종료되지 않음

DNS rebinding 가정 Host header도 실패해야 한다.

```bash
curl -i -H 'Host: attacker.example' http://127.0.0.1:8787/api/health
```

기대 결과:

- 403 `FORBIDDEN_HOST`

명령 실행 우회도 실패해야 한다.

```python
run_command(tmp_path, ["bash", "-lc", "rm -rf ."], policy=CommandPolicy())
run_command(tmp_path, [sys.executable, "-c", "import shutil; shutil.rmtree('.')"], policy=CommandPolicy())
```

기대 결과:

- `PermissionError`

---

## 14. 완료 기준

릴리스 전 아래 조건을 모두 만족해야 한다.

- [ ] `/api/session/end`를 포함한 mutating API는 token 없이 호출 불가
- [ ] Host/Origin 검증이 활성화됨
- [ ] LLM tool schema에 `allow_network`, `allow_destructive`가 노출되지 않음
- [ ] driver가 project safety로 정책 flag를 무조건 덮어씀
- [ ] shell/interpreter 우회 명령이 차단되거나 sandbox runner로 격리됨
- [ ] SSE `queue.get()`에 timeout이 있고 keepalive가 전송됨
- [ ] SSE event id/replay/gap 처리가 구현됨
- [ ] public API 응답에서 traceback, raw exception, 내부 경로가 제거됨
- [ ] `WebConfig().llm_router == WebConfig.from_env().llm_router`
- [ ] preview가 AppForge API와 권한을 공유하지 않음
- [ ] API key 저장 정책이 문서화되고, 가능하면 keychain backend가 제공됨
- [ ] web bundle 관리 정책이 CI로 검증됨

---

## 15. 권장 최종 이슈 제목

1. `[P0][Security] Add local session token, Host, and Origin guard to FastAPI web server`
2. `[P0][Security] Enforce tool safety flags server-side and hide policy flags from LLM schema`
3. `[P0][Security] Replace command denylist boundary with allowlist/sandboxed execution`
4. `[P1][Stability] Fix SSE blocking subscriber.get with timeout keepalive`
5. `[P1][Stability] Add SSE event ids, reconnect replay, and queue gap signaling`
6. `[P1][Security] Redact internal exceptions and traceback from public API payloads`
7. `[P2][Config] Align WebConfig llm_router defaults`
8. `[P2][Security] Isolate generated app preview origin and tighten CSP`
9. `[P2][Secrets] Add optional OS keychain backend for LLM credentials`
10. `[P3][Build] Define CI policy for committed web bundle assets`

---

## 16. 구현 시 주의사항

- CSRF 방어를 cookie만으로 구현하지 않는다. Same-origin preview가 자동 cookie를 이용해 API를 호출할 수 있기 때문이다.
- token을 localStorage보다 sessionStorage에 저장한다. 브라우저 세션 종료 시 자연스럽게 사라지는 편이 로컬 도구에 적합하다.
- preview에는 token을 주입하지 않는다.
- `allow_destructive`와 `allow_network`는 사용자/프로젝트 설정에서 내려오는 정책이지, 모델이 요청하는 인자가 아니다.
- denylist는 보조 방어일 뿐 최종 보안 경계가 아니다. 신뢰할 수 없는 코드의 test/build/install은 sandbox에서 실행하는 방향으로 수렴한다.
- 오류 상세는 로그에는 남기되 API 응답과 UI에는 일반화한다.
- SSE 이벤트 유실은 완벽히 없애기보다, 유실을 감지하고 snapshot으로 회복할 수 있게 만드는 것이 핵심이다.
