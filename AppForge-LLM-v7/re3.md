# re3.md — LLM 기반 파이프라인 라우팅 수정 계획

## 1. 배경과 핵심 문제

### 1.1 의도(원래 설계 목표)
사용자가 "어떤 앱을 만들라"는 프롬프트를 입력하면:
1. 지정/연결한 **LLM 모델이 프롬프트를 판단**하고
2. 그 판단 결과에 따라 **적용할 파이프라인을 다르게 선택**하여 진행한다.

이것이 이 시스템의 핵심 동작이다.

### 1.2 현재 구현의 치명적 누락
재조사 결과, 핵심이 빠져 있다.

- `appforge/pipelines.py:84` `classify_prompt_complexity()`:
  한국어/영어 **키워드 문자열 포함 매칭(정규식 아닌 단순 `in`) 기반 휴리스틱**.
  LLM은 전혀 호출되지 않는다.
- `appforge/pipelines.py:129` `auto_select_pipeline()`:
  키워드 점수 + 하드룰 키워드 매칭으로 최종 파이프라인을 고른다.
  마찬가지로 LLM 무관.
- `appforge/web_jobs.py:330` `create_job()`:
  `auto_select_pipeline(normalized, existing_repo=False)` 로 라우팅.
  LLM 브릿지 URL/provider/model이 인자로 전달되지 않는다.

즉, "LLM이 판단해서 파이프라인을 다르게 적용"이라는 핵심 동작이 코드에 없다.

### 1.3 증거(재현)
- `간단한 타이머 앱 만들어줘` → `web-app-simple` (간단 신호 적중)
- `단순한 타이머 앱 만들어줘` → `web-app` (단순/단순한 신호 누락 → 긴 파이프라인)
- `타이머 앱 만들어줘` → `web-app`

의미상 단순 앱이어도 명시 키워드가 안 맞으면 기존 긴 파이프라인으로 간다.
근본 원인은 키워드 기반 판단이지 단순한 단어 누락이 아니다.

### 1.4 기반 시설은 이미 있음
- `appforge/llm_bridge.py:208` `generate()`: 로컬 브릿지 `/generate` 호출 → `{"text": "..."}` 반환.
- `appforge/drivers.py:418` `LLMBridgeDriver.run()`:이미 `llm_bridge.generate()`로 모델 호출 후 `drivers.py:76` `_extract_json_object()`로 JSON 파싱 중.
- `appforge/web_jobs.py:248` `_llm_bridge_readiness()`: 브릿지 ping + active provider/model 확인 로직 보유.
- `appforge/llm_bridge_process.py`: 브릿지 자동 시작 관리자 존재.

인프라는 다 있고, 라우팅 결정점만 LLM으로 교체하면 된다.

---

## 2. Definition of Done (강 목표)

> "LLM이 프롬프트를 판단 → 판단 결과에 따라 파이프라인이 다르게 적용되는" 동작이
> 단위/회귀 테스트로 검증 가능하고, LLM 브릿지가 unavailable일 때는 기존
> 키워드 휴리스틱으로 안전하게 폴백하며, 사용자 관점에서 `단순한 앱` 입력이
> 간소 파이프라인으로 잡히는 회귀 테스트가 웹 job 레벨에 존재한다."

### 2.1 사용자 가시 동작
- `단순한 타이머 앱 만들어줘`, `작은 노트 패드 앱`, `간단한 웹사이트` 등
  자연어 표현이 LLM 판단에 의해 `web-app-simple`로 라우팅된다.
- LLM이 SaaS/다중 사용자/결제 등을 감지하면 `fullstack-saas`로 간다.
- 모바일/CLI/데스크톱/데이터/프로토타입 등 비-웹 계열도 LLM이 직접 고른다.

### 2.2 인수 기준(Acceptance Criteria)
1. LLM 브릿지가 ready일 때: `create_job(prompt)`가 LLM 판단 결과를 사용해
   `pipeline` 필드를 결정한다.
2. LLM 브릿지가 not ready(다운/미설정)일 때: `create_job`이 예외 없이
   기존 휴리스틱으로 폴백하여 동작한다.
3. LLM이 카탈로그 외 이름/불량 JSON/빈 응답을 반환해도 예외 없이 폴백한다.
4. `단순한` 한국어 신호가 폴백 휴리스틱에서도 `web-app-simple`로 잡힌다.
5. 웹 `/api/jobs` 생성 시 `web-app-simple` 프롬프트가 실제 `web-app-simple`
   stage 목록으로 잡히는 회귀 테스트가 존재한다.
6. 라우팅 결정(`router="llm"|"heuristic"`, `reason`)이 job 이벤트에 기록된다.

### 2.3 비기능 요건
- `create_job` 동기 호출 정책: LLM 분류를 동기로 수행(수 초 허용).
  브릿지 not ready면 폴백으로 즉시 진행(불필요한 블로킹/에러 금지).
- SSOT: LLM 결과가 존재하면 LLM 결과가 파이프라인 진실. 없으면 휴리스틱이 진실.
- 관측가능성: 매 라우팅 결정마다 job 이벤트에 `router`, `reason` 남김.
- 보안: 프롬프트/응답에 자격 증명·토큰 로깅 금지(기존 `redact` 정책 준수).
- 호환성: `auto_select_pipeline` 기존 시그니처(키워드 인자) 보존,
  신규 선택 인자는 키워드 전용으로 추가(기존 호출 깨짐 없음).

### 2.4 DoD 검증 명령
```bash
.venv/bin/python -m pytest tests/test_pipelines.py tests/test_llm_router.py tests/test_web_jobs_routing.py -q
.venv/bin/python -m pytest tests/ -q   # 전제 파이프라인/드라이버 회귀
```

---

## 3. 접근 전략(선택된 단일 안)

후보 비교:
- A) **LLM이 전체 파이프라인 직접 선택** ← 선택
- B) LLM은 simple/normal/complex 3분류만, 매핑은 기존 로직 유지
- C) LLM 분류 전용 도메인 언어/임베딩 기반

선택: **A**. 13개 builtin pipeline의 `name`+`description` 카탈로그를 시스템
프롬프트로 주고, LLM이 JSON `{pipeline, tier, reason}` 한 개만 반환.
B는 모바일/CLI 등 비-웹 계열은 여전히 키워드 하드룰에 의존해 근본 누락 잔존.
C는 인프라 과잉·비용/복잡도 증가로 YAGNI 위반.

**폴백 정책**: 브릿지 미가용 시 기존 휴리스틱 폴백(일관성보다 가용성 우선).
LLM 없이도 앱 생성 가능해야 한다.

**실행 시점**: `create_job` 안에서 동기 호출. 상태머신 복잡도 증가를 피한다.

---

## 4. 설계

### 4.1 신규 모듈 `appforge/llm_router.py`
SSOT 책임: "LLM을 통해 프롬프트 → 파이프라인 이름 결정".

```python
# 핵심 인터페이스(의사 코드)
PipelineChoice = tuple[str, str]  # (pipeline_name, reason)

def llm_classify_pipeline(
    prompt: str,
    *,
    bridge_url: str,
    provider: str | None = None,
    model: str | None = None,
    catalog: list[PipelineSpec] | None = None,
    timeout: float = 30.0,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str] | None:
    """LLM 브릿지 /generate 로 파이프라인을 고른다.

    Returns (pipeline_name, reason) 또는 브릿지/응답 문제 시 None(폴백 신호).
    """
```

- 카탈로그 직렬화: `all_pipelines()`에서 `name`, `description`만 뽑아 JSON 배열로
  시스템 프롬프트에 삽입. 갱신 비용/캐싱은 `pipelines.py` `all_pipelines`가 이미 담당.
- 시스템 프롬프트(요지):
  "아래 카탈로그 중 정확히 하나의 `pipeline` 필드값을 고르고,
   복잡도 `tier`는 simple|normal|complex 로, `reason`은 한 줄로 반환.
   그 외 텍스트/마크다운/설명 금지. JSON만 반환."
- 응답 파싱: `drivers.py` `_extract_json_object()` 패턴을 재사용(이동/공개 함수로
  전환 또는 동일 로직을 `llm_router.py`에 두되 SSOT 원칙상 공통 유틸로 격상).
  - 결과 필드: `pipeline`(str, 카탈로그 포함 검증), `tier`(str), `reason`(str).
  - 검증: `pipeline`이 `catalog`의 name 집합에 없으면 폴백.
- 예외 처리:
  - `llm_bridge.BridgeError` → None 반환(폴백 신호). 예외 전파 금지.
  - `json.JSONDecodeError`/스키마 불일치 → None.
  - `cancel_event` 설정 시 즉시 None(이후 상위 레벨이 취소 처리).

### 4.2 `appforge/pipelines.py` 변경
- `auto_select_pipeline()` 시그니처(키워드 인자 보존):
  ```python
  def auto_select_pipeline(
      prompt: str,
      *,
      existing_repo: bool = False,
      bridge_url: str | None = None,
      llm_provider: str | None = None,
      model: str | None = None,
      use_llm: bool = True,
  ) -> tuple[str, dict[str, int]]:
  ```
- 우선순위:
  1. `use_llm` and `bridge_url`: `llm_classify_pipeline(prompt, bridge_url=...)` 호출.
     - 정상 응답 + 카탈로그 검증 통과 → 해당 pipeline name 사용.
     - None → 폴백(아래 2)로 진행.
  2. 폴백: 기존 휴리스틱 로직 그대로(키워드 점수 + complexity tier + hard_rules).
- 반환값은 기존과 동일한 `(winner, scores)` 형식 유지(호환성).
  단, LLM 선택 시 `scores`에 가상 점수 `{name: 1000}`와 `reason`은 반환 불가하므로
  scores dict에 `__router="llm"`, `__reason=reason` 메타를 추가(선택적). 또는
  반환 튜플 확장 검토: `(winner, scores, router_info)`. ← **선택**: 기존 호출 호환을
  위해 `(winner, scores)`는 유지하고 `scores`에 메타 키를 소문자 접두사로 넣되
  기존 max 계산에 영향 주지 않도록 별도 `_router_meta` dict 병합은 지양.
  최종 결정: 별도 반환은 피하고 사이드 채널(모듈 레벨 last-result)은 SSOT 위반.
  → **인터페이스 확장안**: `auto_select_pipeline`은 `(winner, scores)` 유지,
  `web_jobs.create_job`은 라우터 정보를 `llm_router`로부터 직접 얻는다
  (아래 4.3).
- `_SIMPLE_PROMPT_SIGNALS` 보강(폴백 품질): `단순`, `단순한`, `간단한`, `작은`,
  `소규모`, `가벼운` 추가. re1 키워드 버그 동시 폐쇄.

### 4.3 `appforge/web_jobs.py` `create_job` 변경
- 기존 라인 330:
  ```python
  selected, _scores = auto_select_pipeline(normalized, existing_repo=False)
  ```
- 변경:
  ```python
  router, reason = self._route_prompt(normalized)
  selected, _scores = (
      auto_select_pipeline(
          normalized,
          existing_repo=False,
          bridge_url=router["bridge_url"] if router else None,
          llm_provider=router["provider"] if router else None,
          model=router["model"] if router else None,
          use_llm=router is not None,
      )
      if False  # 아래 설명
      else ...
  )
  ```
  > 실제 구현은 단순화: `_route_prompt()` 헬퍼가 readiness를 판단해
  > `auto_select_pipeline`의 LLM 경통로/폴백을 한 번에 위임.
- 헬퍼 `_route_prompt(prompt)`:
  - `self._llm_bridge_readiness()` 결과 `ready`필드가 True일 때만
    `(bridge_url, provider, model)` 튜플 반환.
  - not ready면 None 반환 → `auto_select_pipeline(use_llm=False)` 경로.
- `pipeline` 확정 후 job dict에 새 필드/이벤트:
  ```python
  self._record_event_locked(
      job,
      "pipeline_routed",
      f"파이프라인 {pipeline.name} 선택",
      context={"router": router_name, "reason": reason},
  )
  ```
  - `router_name`: `"llm"` 또는 `"heuristic"`.
  - `reason`: LLM 응답 reason 또는 폴백 메시지(`"llm bridge unavailable"`).
- 기존 `auto_select_pipeline` 동작/반환 형식 보존 → 다른 호출자(`cli.py` 등)
  깨짐 없음.

### 4.4 JSON 파싱 유틸 SSOT
`drivers.py:76` `_extract_json_object()`가 필요. 두 곳에서 쓰임.
- 안: `appforge/util.py`(또는 신규 `appforge/parsing.py`)로 이동·공개.
  `drivers.py`는 그 모듈에서 import.
  - KISS: `util.py`에 `extract_outermost_json_object(text) -> dict | None` 추가,
    drivers.py 기존 비공개 함수는 얇은 wrapper 또는 직접 치환.
- 이동이 refactor 성격이므로, 본 LLM 라우팅 변경과 **분리 커밋** 권장.
  본 작업에서는 `llm_router.py` 내부에 동등 구현을 임시로 두고,
  별도 refactor 패스에서 SSOT 통합. ← 다만 AGENTS.md "no refactor during bugfix"
  때문에 본 패스에서는 **drivers.py 함수를 import 재사용**하지 않고
  `llm_router.py`에 독립 구현(약간 중복 허용), 후속 패스에서 통합.
  - 단, 두 구현이 같은 동작을 해야 회귀 없음 → 동일 알고리즘 복사.

---

## 5. 테스트 계획 (Red → Green)

### 5.1 신규 `tests/test_llm_router.py`
- `FakeBridge`: `/generate` 응답을 Monkeypatch 가능한 `llm_bridge.generate`
  대체. `text` 필드에 JSON 문자열 반환.
- 케이스:
  1. "단순한 타이머 앱 만들어줘" → LLM 응답
     `{"pipeline":"web-app-simple","tier":"simple","reason":"..."}` →
     `llm_classify_pipeline`이 `("web-app-simple", reason)` 반환. (Red: 현재 모듈 없음)
  2. "로그인/결제/다중 사용자 SaaS" → `fullstack-saas`. (Red)
  3. 불량 JSON / 카탈로그 외 이름 → `None`. (Red)
  4. `BridgeError` 발생 → `None`, 예외 전파 없음. (Red)
  5. `cancel_event` 설정 → `None`. (Red)

### 5.2 기존 `tests/test_pipelines.py`
- 기존 3개 테스트 유지(폴백 휴리스틱 검증, `use_llm=False` 인자로).
- 신규 케이스 추가:
  - `단순한 타이머 앱 만들어줘` + `use_llm=False`(폴백) → `web-app-simple`
    (신호 보강 후). (Red: 현재 `web-app`.)
  - 명시적으로 `bridge_url=None` 또는 `use_llm=False` 전달 시 기존 동작 보존
    검증(인수 기준 2).

### 5.3 신규 `tests/test_web_jobs_routing.py` (웹 job 회귀)
- `WebJobManager` 인스턴스 생성 + LLM 브릿지 readiness를 Stub에서 `ready=True`
  로 고정. `llm_bridge.generate` Monkeypatch로 `web-app-simple` JSON 반환.
- `create_job("단순한 타이머 앱 만들어줘")` → job 저장 후:
  - `job["pipeline"] == "web-app-simple"`
  - `job["stages"]` 이름 목록이 `load_pipeline("web-app-simple")`과 일치.
  - `job["events"]` 중 `pipeline_routed` 이벤트 존재 + `router=="llm"`.
- LLM 브릿지 readiness가 `ready=False`일 때(Stub에서 Ping 실패 시뮬레이션):
  - `create_job("간단한 랜딩페이지 만들어라")` → job 정상 생성,
    `pipeline == "web-app-simple"`(폴백 휴리스틱, 신호 보강 후).
  - `pipeline_routed` 이벤트 `router=="heuristic"`.

### 5.4 실행 순서
1. tests/test_llm_router.py 작성 → Red(모듈 미존재).
2. appforge/llm_router.py 구현 → Green.
3. tests/test_pipelines.py 신규 케이스 → Red.
4. pipelines.py 변경(`_SIMPLE_PROMPT_SIGNALS` 보강, `auto_select_pipeline`
   LLM 우선/폴백) → Green.
5. tests/test_web_jobs_routing.py → Red.
6. web_jobs.py `create_job` + `_route_prompt`/이벤트 → Green.
7. 전제 회귀 + lint + typecheck.

---

## 6. 변경 파일 범위

| 파일 | 변경 유형 | 요약 |
|---|---|---|
| `appforge/llm_router.py` | 신규 | LLM 분류 + JSON 파싱 헬퍼, BridgeError/불량 응답 폴백 |
| `appforge/pipelines.py` | 수정 | `auto_select_pipeline` LLM 우선/폴백; `_SIMPLE_PROMPT_SIGNALS` 확장(단순/단순한/작은/소규모/가벼운/간단한) |
| `appforge/web_jobs.py` | 수정 | `create_job`에서 브릿지 설정 전달, `_route_prompt` 헬퍼, `pipeline_routed` 이벤트 기록 |
| `tests/test_llm_router.py` | 신규 | LLM 분류 단위 테스트(FakeBridge) |
| `tests/test_web_jobs_routing.py` | 신규 | 웹 job 레벨 라우팅 회귀 |
| `tests/test_pipelines.py` | 수정 | `단순한` 케이스 추가, `use_llm=False` 보존 검증 |
| `re3.md` | 본 문서 | 계획 기록 |

---

## 7. 불변량(Invariants) 준수
- SSOT: 라우팅 결정 진실은 LLM 결과(있을 때) 한 곳, 폴백(없을 때) 한 곳.
  두 경로가 동시에 진실을 주장하지 않도록 `use_llm` 플래그로 경로 단일화.
- SoC: LLM 호출은 `llm_router`, 휴리스틱은 `pipelines`, 웹 임팩트는 `web_jobs`.
  레이어 분리 유지.
- YAGNI: 카탈로그 직렬화는 name+description만. 임베딩/캐싱/점수 가중치 미구현.
- No abstraction before second use: `llm_router`는 단일 진입점 함수 1개만
  공개. 범용 "LLM 결정 프레임워크" 추상 도입 금지.
- No refactor during bugfix: `drivers.py` `_extract_json_object` 이동은 본 패스
  외 후속 refactor 패스로 이관. 본 패스는 `llm_router` 내 동등 구현 허용.
- 호환성 보존: `auto_select_pipeline` 기존 키워드 인자 유지, 신규는 키워드 전용.
- 관측가능성: 매 라우팅 결정마다 `pipeline_routed` 이벤트 + `router`/`reason`.
- 보안: 프롬프트·응답 로깅 시 `redact` 적용(기존 정책 준수), 자격증명 미포함.

---

## 8. 리스크
- **블로킹**: LLM 동기 호출이 `create_job`에 수 초 지연 가능.
  완화: 브릿지 not ready면 폴백 즉시 진행, ready여도 timeout=30s 상한.
- **LLM 오분류**: 단순 앱을 SaaS로 과대평가/반대 사례.
  완화: `reason` 기록, 폴백 안전망, 카탈로그 외 이름 거부.
- **카탈로그 Drift**: 신규 pipeline 추가 시 LLM 시스템 프롬프트 자동 반영은
  `all_pipelines()`에 의존 → YAML 추가만으로 LLM 선택지에 들어감(OK).
- **테스트 플레이크**: 외부 LLM을 실제 호출하는 테스트는 금지, FakeBridge만 사용.
- **import 순환**: `llm_router` ↔ `pipelines` 순환 가능.
  완화: `llm_router`는 `pipelines`에서 import만, 역방향 없게 설계.
  `pipelines.auto_select_pipeline`은 지연 import로 `llm_router` 호출.

---

## 9. 승인 게이트
- 본 패스는 코드/테스트/문서 변경만. `git push`, PR 생성, 배포, 외부 상태 변경 없음.
- AGENTS.md "단일 에이전트 순차 실행" 준수.
- 외부 LLM 실제 호출은 테스트에서 수행하지 않음(FakeBridge). 수동 검증 시
  사용자가 자체 LLM 설정 후 `create_job` 동작 확인.

---

## 10. 검증 후 결과 기록 템플릿(잔여)
```
Done:
-
Verified:
  - tests/test_llm_router.py
  - tests/test_web_jobs_routing.py
  - tests/test_pipelines.py
  - .venv/bin/python -m pytest tests/ -q
Changed:
  - appforge/llm_router.py
  - appforge/pipelines.py
  - appforge/web_jobs.py
  - tests/...
Risks / Notes:
  -
Next:
  - drivers.py _extract_json_object SSOT 통합(후속 refactor 패스)
```

---

## 11. 참고 코드 위치
- `appforge/pipelines.py:18-90` ComplexityTier, 신호/점수, classify_prompt_complexity
- `appforge/pipelines.py:129-166` auto_select_pipeline(수정 대상)
- `appforge/web_jobs.py:312-379` create_job(수정 대상)
- `appforge/web_jobs.py:248-310` _llm_bridge_readiness(재사용)
- `appforge/llm_bridge.py:208-241` generate(재사용)
- `appforge/drivers.py:76-87` _extract_json_object(참고/후속 통합)
- `appforge/drivers.py:418-488` LLMBridgeDriver.run(기존 LLM 사용 패턴 참고)
- `tests/test_pipelines.py:38-69` 기존 라우팅 테스트(확장 대상)