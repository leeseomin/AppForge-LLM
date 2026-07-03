# AppForge-LLM v5 개선 가이드

> 대상 코드베이스: `AppForge-LLM-v4` (FastAPI 러너 + Bun LLM 브리지 + Vue UI, 12개 파이프라인 정의)
> 작성 기준: v4 소스 전수 검토. 본문에 인용한 파일·라인은 모두 v4 기준 실제 위치입니다.

---

## 0. 요약과 우선순위

v4는 "스테이지별 아티팩트 스키마 검증 + 게이트 + 체크포인트"라는 검증 골격이 잘 잡혀 있습니다. 반면 실행 계층은 **스테이지당 단발(single-shot) JSON 봉투** 방식에 묶여 있어, 모델이 도구를 쓰지 못하고 전체 산출물을 한 번의 completion에 담아야 합니다. 이 구조적 한계가 재시도 품질, 규모 확장성, UX(침묵 시간) 문제의 공통 원인이므로, v5 작업은 아래 순서를 권장합니다.

| 순위 | 과제 | 핵심 파일 | 기대 효과 |
|---|---|---|---|
| P1 | 브리지 tool-use 에이전트 루프 | `llm_bridge/src/server.ts`, `appforge/drivers.py` | 출력 토큰 한계 해소, diff 편집 가능, 중대형 앱 생성 가능 |
| P2 | 실패-수리(targeted repair) 루프 + 자기보고 신뢰 보정 | `appforge/runner.py`, `appforge/prompting.py`, `drivers.py` | 재시도 성공률·비용 개선, "no fake success" 원칙 정합성 |
| P3 | 프리뷰 + 대화형 수정 UX + SSE 푸시 | `appforge/web_jobs.py`, `frontend/src/*` | 결과 확인/수정 반복 루프 완성, 체감 지연 제거 |
| P4 | LLM 라우팅 + 경량 파이프라인 트랙 | `appforge/pipelines.py`, `pipeline_defs/*.yaml` | 소형 요청 비용 절감, 오분류 감소 |

v4 리뷰 과정에서 확인된 **사실 보정 3가지**를 먼저 기록합니다. 이후 설계가 이 사실 위에 서 있습니다.

1. **`reviewer.md`는 "미사용"이 아닙니다.** `appforge/prompting.py:124`에서 `load_skill("meta/reviewer.md")`로 로드되어 스테이지 프롬프트(163행)에 주입됩니다. 문제의 정확한 정의는 *"같은 completion 안에서의 자기 리뷰만 있고, 독립적인 LLM 리뷰 패스가 없다"*입니다. `gates.py:113`의 `review_stage()`는 게이트 레코드를 severity로 분류해 재포장하는 기계적 집계이며 LLM 호출이 없습니다.
2. **브리지에는 이미 SSE 스트리밍이 있습니다.** `llm_bridge/src/server.ts:37`에 `POST /stream` 라우트가 존재하고 `text/event-stream`으로 이벤트를 내보냅니다(222행 `streamHandler`). Python 쪽 `appforge/llm_bridge.py:generate()`가 `/generate`만 호출할 뿐입니다. 즉 스트리밍 도입은 신규 개발이 아니라 **기존 엔드포인트 소비**입니다.
3. **vendored LLM 엔진에 tool 런타임이 이미 있습니다.** `llm_bridge/vendor/llm/tool.ts`, `tool-runtime.ts`, `protocols/utils/tool-stream.ts`가 존재합니다. P1의 작업량은 "tool-use 엔진 구현"이 아니라 **브리지 프로토콜로 노출 + 러너 측 도구 프록시 루프 작성**입니다.

---

## 1. P1 — 브리지 tool-use 에이전트 루프

### 1.1 현재 구조와 한계 (코드 근거)

현재 실행 경로는 다음과 같습니다.

```
runner.py::_run_stage
  → prompting.py::build_stage_prompt (스킬 + workspace_tree + prior artifacts + 스키마)
  → drivers.py::LLMBridgeDriver.run
      → _build_bridge_prompt: "JSON 봉투만 반환하라"는 계약을 프롬프트 앞에 부착
      → llm_bridge.generate() = POST /generate 단발 호출
      → _extract_json_object: 응답 텍스트에서 JSON 스캔 추출
      → _apply_bridge_envelope: files/artifacts/stage_result 기록
```

이 구조의 결정적 제약 네 가지:

- **출력 토큰 한도 = 앱 규모 상한.** `files`에 모든 소스 본문을 JSON 문자열로 담아야 하므로, 응답이 max_tokens에서 잘리는 순간 `_extract_json_object`가 실패하고 스테이지 전체가 실패합니다. `_find_outermost_json_object`(drivers.py:45)의 중괄호 스캔이 아무리 견고해도 잘린 JSON은 복구 불가입니다.
- **읽기 불가.** 모델은 `workspace_tree`(경로 목록)와 prior artifacts(각 12,000자 truncate, `prompting.py:41`)만 봅니다. 기존 파일 **내용**을 읽을 수단이 없어 diff 편집이 원천적으로 불가능하고, 재시도 시 자기 코드를 못 본 채 통째로 다시 씁니다.
- **실행 불가.** `run_command`, `run_tests` 등은 러너가 게이트로 사후 실행할 뿐, 모델이 구현 중에 컴파일/테스트를 돌려보며 수렴할 수 없습니다. web-app 파이프라인의 implementation 스테이지가 `tools: [run_command, write_text, read_text, ...]`를 선언하지만(web-app.yaml), 이 도구들은 LLM에게 전달되지 않습니다 — 선언과 실행이 분리된 죽은 메타데이터입니다.
- **wrapper 방어 코드의 존재 자체가 증상.** `_unwrap_envelope`의 `_ENVELOPE_WRAPPER_KEYS` 9종 목록(drivers.py:275)은 단발 JSON 계약이 모델에게 얼마나 취약한지를 보여주는 흔적입니다. tool-use로 전환하면 이 계층 전체가 필요 없어집니다.

### 1.2 목표 아키텍처

```
runner.py::_run_stage
  → LLMBridgeAgentDriver.run
      → POST /agent/start {prompt, system, tools: [...스키마]}   (브리지)
      → 루프:
          브리지 SSE 이벤트 수신
            ├─ text_delta      → 로그/UI 스트림 릴레이
            ├─ tool_call       → 러너가 ToolRegistry로 로컬 실행 → POST /agent/{id}/tool_result
            └─ done            → 최종 stage_result / artifacts 수집
      → 게이트/스키마 검증 (기존 gates.py 그대로)
```

핵심 원칙: **도구 실행은 항상 러너(Python) 쪽에서.** 브리지는 provider 프로토콜 변환기로만 남기고, 파일 I/O·명령 실행 권한은 기존 `_safe_workspace_path` 및 `tooling/` 계층의 보안 규칙(`.appforge`/`.git` 쓰기 금지, 경로 탈출 차단)이 그대로 적용되는 Python에 둡니다. 브리지에 파일시스템 접근을 주면 보안 경계가 이중화되어 관리가 어려워집니다.

### 1.3 브리지 측 변경 (`llm_bridge/`)

vendored 엔진의 `tool.ts` 타입을 그대로 노출하는 두 가지 방식이 있습니다.

**방식 A — 상태 유지 세션 (권장).** `/agent/start`가 세션 ID를 발급하고, 브리지가 대화 히스토리(assistant tool_call ↔ tool_result 쌍)를 메모리에 유지합니다. 러너는 tool_result만 밀어 넣으면 됩니다. provider별 히스토리 직렬화 차이를 브리지가 흡수하므로 Python 쪽이 단순해집니다. 세션 TTL(예: 30분)과 단일 활성 세션 수 제한을 두고, 러너 프로세스 재시작 시 세션 유실은 스테이지 재시도로 처리합니다.

**방식 B — 무상태 반복 호출.** 매 턴 러너가 전체 messages 배열을 `/generate`에 보내는 방식. 구현이 단순하지만 히스토리가 매번 네트워크를 오가고, provider별 tool 메시지 포맷 차이를 Python이 알아야 합니다. 로컬 루프백 통신이라 성능 문제는 크지 않으므로, 세션 관리 코드를 피하고 싶다면 B로 시작해 A로 이행해도 됩니다.

브리지 API 초안(방식 A):

```
POST /agent/start
  { system, prompt, provider?, model?, generation?,
    tools: [{ name, description, parameters: <JSON Schema> }] }
  → { session_id }

GET  /agent/{session_id}/events        (SSE)
  event: text_delta   data: { text }
  event: tool_call    data: { call_id, name, arguments }
  event: done         data: { finish_reason, usage }
  event: error        data: { message, code }

POST /agent/{session_id}/tool_result
  { call_id, result: <string|object>, is_error?: bool }

DELETE /agent/{session_id}
```

`server.ts`의 `ROUTES` 배열에 라우트 4개를 추가하고, `llm.ts`의 `stream()`을 tool 이벤트 분기(`tool-stream.ts` 활용)와 함께 재사용하면 됩니다. `bun.lock` 기준 외부 의존성 추가는 필요 없습니다.

### 1.4 러너 측 변경 (`appforge/`)

**1) 도구 노출 어댑터.** `tooling/registry.py`의 `ToolRegistry`는 자동 발견(`discover`) 구조라 이미 도구 카탈로그 역할을 합니다. 각 `Tool`에 LLM 노출용 메타데이터를 추가하세요.

```python
# tooling/base.py
class Tool(ABC):
    name: str
    llm_exposed: bool = False          # LLM 에이전트 루프에 노출할지
    llm_description: str = ""          # 모델에게 보여줄 설명
    llm_parameters: dict = {}          # JSON Schema

    def run(self, root: Path, params: dict) -> ToolResult: ...
```

스테이지 YAML의 `tools:` 목록이 이제 실제 의미를 갖게 됩니다 — `_build_agent_tools(stage)`가 `stage.tools ∩ {t for t in registry if t.llm_exposed}`를 브리지에 전달합니다. 최소 노출 세트는 `read_text`, `write_text`, `search_text`, `workspace_tree`, `run_command`(화이트리스트 셸), `run_tests`로 충분합니다.

**2) `LLMBridgeAgentDriver`.** 기존 `LLMBridgeDriver`를 대체하지 말고 **병행 추가**하세요(`create_driver`에 `llm-bridge-agent` 이름 등록, 초기에는 opt-in). 골격:

```python
class LLMBridgeAgentDriver(AgentDriver):
    name = "llm-bridge-agent"
    MAX_TURNS = 40            # 스테이지 성격별로 조정 (구현: 40, 명세류: 8)
    MAX_TOOL_SECONDS = 120    # 도구 1회 실행 상한

    def run(self, prompt, *, layout, stage, attempt, timeout, cancel_event=None):
        tools = build_agent_tools(stage)          # ①
        session = bridge.agent_start(prompt=..., tools=tools)
        turns = 0
        for event in bridge.agent_events(session, cancel_event=cancel_event):
            if event.type == "text_delta":
                self._relay_stream(stage, event.text)          # ② UI/SSE로 릴레이
            elif event.type == "tool_call":
                turns += 1
                if turns > self.MAX_TURNS:
                    return self._fail("AGENT_TURN_BUDGET_EXCEEDED", ...)
                result = self._execute_tool(layout, event)     # ③ 보안 경계 여기서
                bridge.agent_tool_result(session, event.call_id, result)
            elif event.type == "done":
                break
        return self._collect_stage_outputs(layout, stage)      # ④
```

③에서 지킬 규칙: `write_text`/`read_text` 경로는 반드시 기존 `_safe_workspace_path` 로직을 통과시키고, `run_command`는 `tooling/command.py`의 기존 실행기(타임아웃·출력 truncate·redact)를 재사용하며, 도구 결과는 `MAX_CAPTURE_CHARS` 기준으로 잘라 컨텍스트 팽창을 막습니다. 위험 명령(패키지 글로벌 설치, 네트워크 호출)은 러너의 기존 `allow_network`/`allow_destructive` 플래그를 그대로 존중합니다.

**3) 산출물 수집 방식 전환.** 단발 봉투에서는 `files`가 응답에 실려 왔지만, 에이전트 모드에서는 모델이 `write_text`로 직접 파일을 씁니다. 아티팩트는 두 방법 중 하나로 수집합니다.

- (a) 전용 도구 `submit_artifact(name, payload)` 를 노출하고 제출 즉시 `validate_artifact`를 돌려 **실패를 tool_result로 되돌려주는** 방식 — 모델이 같은 세션 안에서 스키마 오류를 즉시 고칠 수 있어 강력히 권장합니다. `submit_stage_result(payload)`도 동일 패턴.
- (b) 세션 종료 후 `.appforge/artifacts/*.json` 존재 여부를 검사하는 방식 — 단순하지만 스키마 실패가 곧 스테이지 실패가 되어 재시도 비용이 큽니다.

(a)를 채택하면 `_unwrap_envelope`, `_ENVELOPE_WRAPPER_KEYS`, `_locate_artifact_payload` 등 봉투 방어 계층 전체가 에이전트 경로에서 사라집니다.

**4) 예산·안전장치.** 턴 수 상한 외에, 세션 누적 토큰 예산(usage 이벤트 합산), 동일 도구+동일 인자 연속 호출 감지(3회 반복 시 tool_result로 경고 주입), `cancel_event` 시 `DELETE /agent/{id}` 정리를 반드시 넣으세요. 이 값들은 스테이지 YAML의 `orchestration:` 아래에 선언 가능하게 하면 파이프라인별 튜닝이 됩니다.

### 1.5 마이그레이션 전략

한 번에 전부 바꾸지 마세요. 위험도가 낮은 순서:

1. **implementation / verification 스테이지만** `llm-bridge-agent`로 전환 (프로젝트 설정 또는 스테이지 YAML `driver:` 필드로 지정). 이 두 스테이지가 단발 방식의 피해가 가장 큰 곳입니다.
2. 명세류 스테이지(intake~experience)는 산출물이 JSON 아티팩트 하나뿐이라 단발 방식이 오히려 저렴합니다 — **유지**하되, `submit_artifact` 도구만 쓰는 2~3턴짜리 미니 에이전트로 바꾸면 스키마 오류 자가 수정 이점을 얻습니다.
3. 안정화 후 `_default_stage_result` 계열 단발 코드를 정리.

### 1.6 수용 기준

- 출력 20파일/3,000줄 규모 웹앱을 implementation 스테이지 1회에 생성 가능 (v4에서는 JSON 절단으로 불가능한 규모).
- 재시도 시 모델이 `read_text`로 기존 파일을 읽고 부분 수정하는 로그가 확인됨.
- 아티팩트 스키마 실패가 스테이지 실패가 아니라 세션 내 수정으로 흡수되는 비율 ≥ 80%.
- 턴/토큰 예산 초과 시 명확한 실패 코드(`AGENT_TURN_BUDGET_EXCEEDED` 등)로 종료하고 좀비 세션이 남지 않음.

---

## 2. P2 — 실패-수리(targeted repair) 루프와 자기보고 신뢰

### 2.1 현재 재시도의 문제

`runner.py::_run_stage`의 재시도는 실패 정보를 `failure` dict로 요약해 **동일한 스테이지 프롬프트를 다시 실행**합니다. `_register_failure_signature`(runner.py:575)가 동일 실패 시그니처 반복을 감지해 `REPEATED_FAILURE_LOOP`를 발생시키지만, 그 `action` 필드는 *"Stop repeating the same repair path. … change the implementation strategy"*라는 **텍스트 지시**일 뿐이고, 실제로 전송되는 프롬프트는 구조적으로 동일합니다. 모델 입장에서 바뀌는 것이 없으므로 가드는 조기 중단 장치로만 기능하고 수리 장치로는 기능하지 않습니다.

여기에 두 가지 컨텍스트 결핍이 겹칩니다.

- `prompting.py`의 스테이지 패킷에는 **실패한 게이트의 실제 로그**(pytest 출력, 빌드 에러)가 들어가지 않습니다. `_remember_attempt`가 `failed_checks`를 메모리(JSONL)에 기록하지만 `reason` 한 줄 요약 수준입니다.
- **파일 내용이 없습니다.** 재시도 프롬프트에도 workspace_tree(경로)와 prior artifacts만 있어, 모델은 자기가 방금 쓴 코드를 못 본 채 처음부터 다시 씁니다. 이것이 "재시도 = 전체 재생성 = 새로운 랜덤 시도"가 되는 직접 원인입니다.

### 2.2 수리 루프 설계

재시도를 두 모드로 분리하세요.

**모드 1 — repair (기본).** 게이트 실패 시 스테이지 프롬프트를 다시 만들지 말고, 별도의 수리 패킷을 구성합니다.

```
# Repair packet (attempt N, stage=implementation)
## Failed gate
tool: run_tests, required: true

## Failure evidence (verbatim, tail 8000 chars)
FAILED tests/test_auth.py::test_login_rejects_expired_token
E   AssertionError: expected 401, got 200
...

## Files implicated (full content)
### src/auth/session.py
```python
<파일 전체>
```
### tests/test_auth.py
<실패 테스트 함수 ±30줄>

## Instructions
- 위 실패만 고치세요. 관련 없는 파일을 다시 쓰지 마세요.
- 수정은 write_text(에이전트 모드) 또는 files에 해당 파일만 담아(단발 모드) 반환하세요.
```

"implicated files" 선정은 휴리스틱으로 충분합니다: 게이트 stderr/stdout에서 `상대경로.py:라인` / traceback 경로 / 컴파일 에러 경로를 정규식으로 추출 → 워크스페이스 내 존재 파일과 매칭 → 상위 N개(예: 6개, 총 40KB 상한). 추출 실패 시 직전 attempt의 `files_changed` 목록으로 폴백합니다.

**모드 2 — regenerate (탈출구).** repair가 `REPEATED_FAILURE_LOOP`에 걸리면 그때 전체 재생성으로 승격하되, 실패 히스토리 요약("이전 2회 시도에서 X 접근이 실패했음, 다른 전략 필요")을 프롬프트에 명시적으로 넣습니다. 즉 시그니처 가드가 "중단"이 아니라 **"전략 전환 트리거"**가 되도록 합니다.

`max_stage_attempts=3`(web-app.yaml)의 의미도 재정의하세요: `1 full + 2 repair`가 `3 full`보다 싸고 성공률이 높습니다. P1의 에이전트 모드가 도입되면 repair는 같은 세션의 후속 턴으로 자연 흡수되므로, 이 설계는 단발 모드의 브리지 역할이기도 합니다.

### 2.3 구현 컨텍스트에 파일 내용 주입

repair와 별개로, **초회 시도부터** implementation 스테이지 패킷에 변경 대상 파일 본문을 넣어야 합니다. `prompting.py`에 추가:

```python
def _relevant_file_contents(layout, stage, budget_chars=60_000) -> str:
    # 우선순위: ① 직전 attempt files_changed ② change_plan/architecture_spec이
    # 지목한 경로 ③ 엔트리포인트 휴리스틱(main/app/index/package.json 등)
    # budget 소진까지 파일 전문 포함, 초과분은 head/tail truncate + 표시
```

기존 `_prior_artifacts`의 12,000자 truncate도 재검토 대상입니다 — architecture_spec처럼 구현이 직접 의존하는 아티팩트는 잘리면 안 되므로, 아티팩트별 truncate 상한을 스키마 메타데이터로 선언하는 편이 안전합니다.

### 2.4 자기보고 신뢰 보정 (`_default_stage_result`)

`drivers.py:176`의 `_default_stage_result`는 JSON 파싱이 성공하기만 하면 다음을 자동 생성합니다.

```python
"checks": [{ "name": "llm-bridge-json-envelope", "passed": True,
             "evidence": "Response parsed and required artifact schemas were validated." }]
```

이는 두 겹으로 문제입니다. 첫째, "파싱 성공"이 "검증 통과"로 둔갑해 프로젝트의 no-fake-success 원칙(AGENTS.md)과 충돌합니다. 둘째, 게이트가 없는 초기 스테이지(intake~loop_engineering은 전부 `gates: []`)는 이 자동 체크만으로 사실상 무검증 통과합니다. 수정 방향:

- `stage-result.schema.json`의 `checks[].passed`를 `boolean | null`로 확장하고, 자동 생성 체크는 `passed: null, "verification": "unverified-self-report"`로 표기. `review_stage`는 `null`을 "suggestion: 독립 검증 없음"으로 집계.
- evidence 문구를 사실만 담게 수정: `"Envelope parsed; artifact schemas validated. No behavioral verification performed."`
- UI(StageTimeline)에서 unverified 체크는 회색 배지로 구분 표시 — 사용자가 "초록불 = 검증됨"으로 오독하지 않게.

### 2.5 독립 LLM 리뷰 패스 (선택이지만 저비용 고효율)

`reviewer.md`가 생성 프롬프트에 주입되는 현재 방식은 "채점자가 답안 작성자와 동일 인격"인 자기 리뷰입니다. implementation·verification 두 스테이지에만, 게이트 통과 **후** 저비용 모델로 독립 리뷰 1콜을 추가하세요.

```
입력: requirements_spec 요약 + files_changed diff(또는 파일 목록+핵심 파일) + stage_result
출력(스키마 고정): { "verdict": "pass|concerns|block",
                    "findings": [{severity, file, finding, proposed_fix}] }
```

`verdict: block`은 repair 루프의 입력으로 연결하고, `concerns`는 handoff_report의 known-issues로 흘려보냅니다. 리뷰 모델은 ProviderSettings에서 별도 지정 가능하게(예: 생성은 대형 모델, 리뷰는 소형 모델) 하면 비용 통제가 됩니다.

### 2.6 수용 기준

- run_tests 실패 → repair 1회로 통과하는 비율이 전체 재생성 대비 유의미하게 상승 (내부 벤치: 동일 프롬프트 10건 반복 비교).
- repair 프롬프트 토큰이 full 스테이지 프롬프트의 40% 이하.
- 게이트 없는 스테이지의 stage_result에 `passed: true` 자동 체크가 더 이상 생성되지 않음.
- REPEATED_FAILURE_LOOP 발생 시 다음 시도의 프롬프트가 실제로 달라짐(전략 전환 섹션 포함)을 로그로 확인 가능.

---

## 3. P3 — 프리뷰, 대화형 수정, 이벤트 푸시 UX

### 3.1 결과 프리뷰

현재 최종 산출은 handoff 스테이지의 `archive_workspace` ZIP뿐이라, 사용자는 압축 해제·의존성 설치·빌드를 직접 해야 결과를 봅니다. 자율 빌더의 핵심 루프(생성 → 확인 → 수정 요청)의 "확인"이 통째로 빠져 있는 셈입니다. 단계적으로:

**3.1a 파일 트리 + 코드 뷰어 (필수, 저비용).** `web.py`에 읽기 전용 엔드포인트 2개를 추가합니다.

```
GET /api/jobs/{id}/workspace/tree            → 기존 workspace_tree 도구 재사용
GET /api/jobs/{id}/workspace/file?path=...   → _safe_workspace_path 검증 + 텍스트만 +
                                                크기 상한(예: 512KB) + MIME 화이트리스트
```

JobPanel(현재 160줄)에 파일 트리 사이드바와 하이라이트 뷰어(경량 라이브러리: highlight.js 또는 Shiki)를 붙입니다. 이것만으로 "ZIP 깜깜이" 문제의 대부분이 해소됩니다.

**3.1b 정적/웹앱 iframe 프리뷰.** 빌드 산출물이 정적인 파이프라인(web-app, prototype)에 한해:

```
POST /api/jobs/{id}/preview/build   → 워크스페이스에서 run_build 실행, dist/ 탐지
GET  /preview/{id}/{path}           → dist 정적 서빙
```

프런트에서 `<iframe sandbox="allow-scripts" src="/preview/{id}/">`로 표시합니다. **`allow-same-origin`을 함께 주지 마세요** — 생성된(=신뢰 불가) 코드가 러너 API 오리진의 쿠키·API에 접근하게 됩니다. 이상적으로는 프리뷰를 별도 포트/서브도메인에서 서빙해 오리진 자체를 분리하세요. dev-server가 필요한 스택(Vite 등)은 v5 범위에서는 "빌드된 정적 산출물만 프리뷰"로 한정하는 것이 안전하고, 컨테이너 기반 실행 프리뷰는 v6 과제로 미루기를 권합니다.

### 3.2 대화형 수정 루프

프리뷰를 보고 "버튼 색을 바꿔줘"를 던질 수 있어야 루프가 닫힙니다. 다행히 재료가 이미 있습니다 — `feature.yaml`/`bugfix.yaml` 파이프라인과 `runner.run(only_stage=...)`. 신규 개념을 만들지 말고 이를 조합하세요.

```
POST /api/jobs/{id}/revise  { "request": "로그인 버튼을 파란색으로" }
  → 동일 워크스페이스를 대상으로 feature(또는 bugfix) 파이프라인의 신규 잡 생성
  → repository_analysis 스테이지가 기존 코드를 읽고 change_plan 생성
  → UI에는 원 잡 아래에 "수정 #1" 으로 체이닝 표시
```

`web_jobs.py`가 현재 단일 활성 잡 전제이므로, 잡에 `parent_job_id`와 `workspace_ref` 필드를 추가해 워크스페이스 공유 계보를 기록해야 합니다. 수정 잡은 원 잡의 아티팩트(requirements_spec 등)를 prior context로 승계하면 품질이 크게 올라갑니다.

### 3.3 중간 아티팩트 노출과 승인 UX

- **아티팩트 뷰어:** StageTimeline(50줄)의 각 스테이지 항목을 펼치면 `GET /api/jobs/{id}/artifacts/{name}`으로 해당 JSON을 가져와 스키마 기반 요약 렌더링(제목·요구사항 ID 테이블 등) + raw JSON 토글로 표시. 검증 신뢰는 "보여줄 때" 생깁니다.
- **승인 흐름 복원:** web-app.yaml에서 architecture·experience·release가 `approval: true`인데 `web_jobs.py:590`이 `auto_approve=True`로 전부 우회합니다. 잡 생성 시 모드 선택(`autonomous` | `checkpoint`)을 노출하고, checkpoint 모드에서는 `stage_awaiting_approval` 이벤트(이미 web_jobs.py:864에서 처리됨)를 받아 타임라인에 [아티팩트 검토] → [승인/수정 요청] 카드를 띄우세요. "수정 요청" 텍스트는 해당 스테이지의 repair 프롬프트로 주입하면 P2와 자연스럽게 연결됩니다.

### 3.4 폴링 → SSE 전환

`App.vue`는 `pollTimer = setTimeout(...)` 재귀 폴링입니다(App.vue:126–128 외 6곳). 러너에 SSE 엔드포인트 하나를 추가하세요.

```
GET /api/jobs/{id}/events   (text/event-stream)
  event: stage_started / stage_retrying / loop_guard_triggered /
         stage_awaiting_approval / stage_failed / job_completed
  event: llm_text   data: { stage, delta }     ← P1 에이전트 스트림 릴레이
  event: tool_call  data: { stage, name }      ← "지금 테스트 실행 중" 표시용
```

`runner._emit`이 이미 이벤트 소스이므로, 잡별 `asyncio.Queue`(또는 스레드 안전 큐)로 브리지하면 됩니다. FastAPI에서는 `StreamingResponse` 또는 `sse-starlette`로 간단히 구현됩니다. 프런트는 `EventSource` + 폴링 폴백(연결 실패 시 기존 pollTimer 유지)을 두면 마이그레이션 리스크가 없습니다. `llm_bridge`의 기존 `/stream`(server.ts:222)을 Python `llm_bridge.py`에 `stream()` 클라이언트로 추가 소비하면, 구현 스테이지 수 분간의 "침묵"이 실시간 토큰 표시로 바뀝니다 — 체감 품질에서 가장 가성비 좋은 항목입니다.

### 3.5 온보딩과 실패 복구 UX

- **ProviderSettings(774줄) 분해:** 최초 진입 시 키/OAuth 미설정이면 3단계 마법사(① provider 선택 → ② 인증 → ③ 모델 선택 + 테스트 콜)로 유도하고, 전체 설정 화면은 "고급"으로 격하. 774줄 컴포넌트는 `ProviderList` / `AuthPanel` / `ModelPicker` / `OnboardingWizard`로 분리.
- **HealthBanner 액션화:** 진단별 버튼 직결 — 브리지 다운이면 [브리지 시작](기존 `llm_bridge_process.py` 스폰 재사용), 키 없음이면 [키 입력](마법사 2단계로 점프), 모델 미선택이면 [모델 선택].
- **ErrorPanel 드릴다운:** 실패 시 `failure` dict에 이미 `code / message / action / loop_signature`가 있으므로 그대로 구조화 표시하고, 스테이지 attempt 로그(`layout.logs`)를 여는 링크 + **[이 스테이지부터 재시도]** 버튼(`only_stage` 그대로 사용)을 추가. repair 루프(P2) 도입 후에는 [자동 수리 시도] 버튼으로 승격.

### 3.6 수용 기준

- 잡 완료 후 클릭 2회 이내에 생성 앱 화면(또는 최소 코드)을 볼 수 있음.
- "수정 요청 → 반영 확인"이 새 프롬프트 작성 없이 동일 화면에서 완결.
- 구현 스테이지 진행 중 30초 이상 UI 무변화 구간이 없음(토큰 스트림 또는 tool_call 표시).
- 신규 사용자가 문서 없이 첫 잡 실행까지 도달(마법사만으로 인증 완료).

---

## 4. P4 — LLM 라우팅과 경량 파이프라인 트랙

### 4.1 라우팅 교체

현재 라우터(`pipelines.py:55–75`)는 키워드 부분 문자열 스코어링(공백 포함 키워드 2점, 단일어 1점) + `hard_rules` 우선 규칙입니다. "장부 정리 프로그램", "재고 관리 도구" 같은 표현은 어떤 키워드에도 걸리지 않거나 엉뚱한 규칙("도구" → cli-tool 유사 매칭)에 걸립니다. 특히 `hard_rules`의 `"api"` 니들은 "API 연동되는 웹앱"조차 api-service로 끌고 갑니다.

**저비용 LLM 1콜 분류기**로 교체하되, 구조는 다음을 권장합니다.

```
입력: 사용자 프롬프트 + 12개 파이프라인의 {name, description, 대표 예시 2개}
출력(스키마 고정): { "pipeline": "...", "confidence": 0.0~1.0,
                    "complexity": "trivial|small|standard|complex",
                    "rationale": "한 문장" }
```

운용 규칙: `confidence < 0.6`이면 키워드 스코어러를 타이브레이커로 병용하고 UI에 후보 2개를 함께 제시. 분류 결과와 `rationale`을 잡 생성 확인 화면에 노출해 사용자가 시작 전에 파이프라인을 바꿀 수 있게 하세요(잘못된 라우팅의 비용은 파이프라인 전체 비용이므로, 정정 기회 한 번이 분류기 정확도 몇 %p보다 값집니다). 분류 콜은 브리지의 저비용 모델 슬롯(리뷰 모델과 공유)으로 보냅니다. 브리지 미가동 등 분류 실패 시 기존 키워드 라우터 폴백을 유지해 가용성을 지킵니다.

### 4.2 경량 트랙: `engineering_spec` 접기

web-app 파이프라인은 코드가 나오기 전에 intake → specification → workflow_design → memory_engineering → loop_engineering → architecture → experience, 7개 문서 스테이지를 지납니다. "투두앱 만들어줘"도 동일 비용입니다. 분류기의 `complexity`를 트랙 선택에 연결하세요.

- **trivial/small → `web-app-lite`(신규 YAML):** `intake → engineering_spec → implementation → verification → handoff`. `engineering_spec` 스테이지는 requirements 요점 + 상태/저장 설계 + 화면 목록을 **하나의 신규 아티팩트 스키마**(`engineering_spec.schema.json`, 기존 4개 스키마의 필수 필드만 발췌한 축약형)로 산출합니다. security·release는 게이트만 verification에 흡수(`secret_scan`을 verification 게이트로 이동).
- **standard → 기존 web-app**, **complex → fullstack-saas** 유지.
- `prototype.yaml`은 lite 트랙과 역할이 겹치므로, "일회성 데모(테스트 게이트 완화)" 용도로 재정의하거나 lite에 통합해 파이프라인 수를 줄이는 것도 검토하세요.

새 아티팩트 하나 + YAML 하나 + 스키마 하나로 끝나는 작업이라, 기존 `load_pipeline`/게이트 코드는 무수정입니다. 효과 측정: 동일 소형 요청에 대해 스테이지 수 12→5, LLM 콜 수와 총 토큰을 잡 요약에 기록해 비교하세요(web_jobs가 usage를 수집하도록 P1에서 usage 이벤트를 이미 확보).

---

## 5. 그 외 정비 항목

**structured output 활용.** `_extract_json_object`의 문자열 스캔은 최후 방어선으로 남기되, 브리지 `/generate`·`/agent`에 `response_format: json_schema`(OpenAI 계열) / `output_schema`(일부 provider) 패스스루를 추가하세요. vendored 프로토콜 계층(`openai-chat.ts`, `anthropic-messages.ts`)에 옵션 필드만 뚫으면 됩니다. 지원 provider에서는 wrapper 문제와 fence 파싱이 원천 제거됩니다.

**동시 잡.** 단일 활성 잡 제한은 P3의 수정 체이닝과 충돌합니다. 잡 큐(동시 실행 1, 대기열 N)만이라도 먼저 도입하면 UX가 매끄러워지고, 이후 워크스페이스 격리를 확인한 뒤 동시 실행 2~3으로 올리면 됩니다.

**로그·재현성.** 에이전트 모드 도입 시 `{stage}-attempt-{n}` 로그에 tool_call/tool_result 전문(redact 적용)을 JSONL로 남기세요. 단발 모드의 `-llm-bridge-final.txt`에 대응하는 감사 추적이며, P2의 implicated-files 추출기와 리플레이 디버깅의 입력이 됩니다.

**테스트 전략.** `tests/test_runner.py`의 페이크 드라이버 패턴을 에이전트 드라이버에도 적용하되, "스크립트된 tool_call 시퀀스"를 재생하는 `ScriptedAgentBridge` 픽스처를 만들면 네트워크 없이 루프 로직(턴 예산, 반복 감지, 취소)을 검증할 수 있습니다. 브리지 쪽은 `test/server.test.ts`에 `/agent/*` 계약 테스트를 추가합니다.

**보안 체크리스트(신규 표면).**

- 에이전트 `run_command`: 화이트리스트/타임아웃/출력 상한 — 기존 `command.py` 재사용으로 충족.
- 프리뷰 iframe: `allow-same-origin` 금지, 가급적 별도 오리진, `Content-Security-Policy` 헤더.
- 워크스페이스 파일 서빙: `_safe_workspace_path` 통과 + `.appforge`·`.git` 차단 + 심볼릭 링크 해석 후 검증(기존 `resolve()` 방식 유지).
- SSE 엔드포인트: 잡 소유 검증(현재 인증 모델 기준) 및 이벤트 페이로드 redact.

---

## 6. 로드맵 제안

| 단계 | 범위 | 산출물 |
|---|---|---|
| M1 (기반) | 브리지 `/agent/*` + `LLMBridgeAgentDriver` + implementation/verification 전환, tool 감사 로그 | 중형 앱 생성 성공, 턴 예산 가드 |
| M2 (품질) | repair 루프 + 파일 컨텍스트 주입 + `_default_stage_result` unverified 표기 + 독립 리뷰 패스 | 재시도 성공률 지표, no-fake-success 정합 |
| M3 (UX) | SSE 이벤트/토큰 스트림 + 파일 트리·코드 뷰어 + ErrorPanel 드릴다운 + only_stage 재시도 버튼 | 침묵 구간 제거, 복구 UX |
| M4 (루프) | iframe 프리뷰 + revise 체이닝(feature/bugfix 재사용) + 승인 UX 복원 | 대화형 빌드 루프 완성 |
| M5 (효율) | LLM 라우터 + web-app-lite 트랙 + structured output + 온보딩 마법사 | 소형 요청 비용 절감, 신규 사용자 진입 |

M1·M2가 서로 의존하고(에이전트 세션 = repair의 자연 서식지), M3은 독립적이라 병행 가능합니다. M4는 M1+M3 완료 후, M5는 언제든 독립 착수 가능합니다.

---

## 부록 A. 파일별 변경 지도

| 파일 | 변경 |
|---|---|
| `llm_bridge/src/server.ts` | `/agent/start`, `/agent/{id}/events`, `/agent/{id}/tool_result`, `DELETE /agent/{id}` 라우트 추가 |
| `llm_bridge/src/llm.ts` | `stream()`에 tool 이벤트 분기 노출 (`vendor/llm/tool-stream.ts` 활용) |
| `appforge/llm_bridge.py` | `stream()`·`agent_*()` 클라이언트 추가 (SSE 소비) |
| `appforge/drivers.py` | `LLMBridgeAgentDriver` 신설, `_default_stage_result` unverified 표기, structured-output 옵션 패스스루 |
| `appforge/tooling/base.py` | `llm_exposed / llm_description / llm_parameters` 메타데이터 |
| `appforge/prompting.py` | `_relevant_file_contents()` 추가, repair 패킷 빌더 분리 |
| `appforge/runner.py` | repair/regenerate 재시도 모드, 실패 로그 추출기, SSE 이벤트 큐 브리지 |
| `appforge/gates.py` | `checks[].passed=null` 집계, 독립 리뷰 verdict 연동 |
| `appforge/pipelines.py` | LLM 분류기 + 키워드 폴백, complexity → 트랙 매핑 |
| `appforge/resources/pipeline_defs/web-app-lite.yaml` | 신규 경량 파이프라인 |
| `appforge/resources/schemas/artifacts/engineering_spec.schema.json` | 신규 축약 아티팩트 |
| `appforge/web.py` / `web_jobs.py` | `/events`(SSE), `/workspace/tree·file`, `/preview/*`, `/revise`, 잡 체이닝(`parent_job_id`), 모드 선택 |
| `frontend/src/*` | EventSource 클라이언트, 파일 트리/코드 뷰어, 아티팩트 뷰어, 승인 카드, 온보딩 마법사, ProviderSettings 분해 |

## 부록 B. v4 리뷰 대비 사실 보정 요약

원 리뷰의 지적은 대부분 코드로 확인되었으며, 다음 세 항목만 표현을 보정합니다: ① reviewer.md는 미사용이 아니라 동일 completion 내 자기 리뷰로만 사용(독립 리뷰 패스 부재가 정확한 갭), ② 스트리밍은 브리지에 이미 구현되어 있고 Python 소비부만 부재, ③ tool-use 엔진은 vendored llm에 이미 존재하므로 P1의 실작업은 프로토콜 노출과 러너 프록시 루프 작성임.
