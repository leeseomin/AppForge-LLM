검토 결과, **방향성은 맞습니다.** 현재 코드와 화면 구조는 “프롬프트 입력 → AI가 단계별로 기획/구현/검증 → 산출물 확인 → ZIP 다운로드” 흐름을 갖고 있어서, 말씀하신 **“just a prompt로 앱을 만드는 autonomous AI agent service”** 성격과 잘 맞습니다.

다만 지금은 사용자에게 보이는 메시지와 일부 구현이 아직 **“개발자용 로컬 Vite/Vue 파이프라인 도구”** 느낌이 강합니다. 제품으로 보이게 하려면 **브랜딩/카피를 비개발자 친화적으로 바꾸고**, 동시에 몇 가지 실제 동작 버그와 안전장치를 보완하는 것이 좋겠습니다.

확인 범위는 ZIP을 풀어서 `README`, `README.ko.md`, FastAPI 백엔드, Vue 프론트엔드, LLM bridge, runner/tooling 쪽을 정적 검토했고, `python -m compileall`과 `pytest`는 통과했습니다. 프론트엔드 `vue-tsc`/빌드는 `node_modules`가 없어 실행하지 못했습니다.

---

## 1. 제품 포지셔닝 문구를 더 직접적으로 바꾸는 것이 좋습니다

현재 첫 화면 문구가 다음처럼 되어 있습니다.

`V6 AGENTIC ENGINEERING · VITE + VUE UX`
`설명만 입력하면 완성된 소스 ZIP까지.`

기능은 맞지만, 고객 입장에서는 `V6`, `Vite`, `Vue UX`가 먼저 보이면 “앱 제작 AI 서비스”보다 “개발자 내부 도구”처럼 느껴집니다. 사용자에게는 기술 스택보다 결과를 먼저 보여주는 편이 좋습니다.

추천 문구는 이런 방향입니다.

```text
AUTONOMOUS AI APP BUILDER

프롬프트 하나로 앱 기획부터 구현·검증·ZIP까지

원하는 앱을 설명하면 AI 에이전트가 요구사항을 정리하고,
코드를 작성하고, 테스트/빌드를 실행한 뒤
프리뷰와 소스 ZIP으로 결과를 제공합니다.
```

CTA도 현재 `앱 만들기`보다 조금 더 서비스 성격을 드러내면 좋습니다.

```text
AI 에이전트로 앱 만들기
```

실행 모드는 현재도 좋지만, 더 명확하게 바꾸면 좋습니다.

```text
완전 자율 실행
검토 없이 끝까지 자동 제작

검토 후 진행
중간 산출물을 확인하고 승인한 뒤 계속 진행
```

영문 한 줄 포지셔닝은 이렇게 잡을 수 있습니다.

```text
An autonomous AI app-building agent that turns one prompt into a planned, tested, previewable source package.
```

한국어 한 줄은 이렇게 추천합니다.

```text
프롬프트만 입력하면 AI 에이전트가 앱을 기획·구현·검증하고, 프리뷰와 소스 ZIP으로 완성해 주는 앱 제작 서비스.
```

---

## 2. “자율 에이전트”라고 말하려면 실제 동작 범위를 더 명확히 보여줘야 합니다

코드를 보면 `implementation`, `verification`, `fix`, `regression` 단계는 tool-use agent 루프로 동작하는 구조입니다. 반면 전략/기획 쪽은 structured output 중심입니다. 이 자체는 나쁘지 않습니다. 오히려 안전하고 제품화하기 좋은 방식입니다.

다만 마케팅 문구에서 “완전한 자율 에이전트가 모든 것을 알아서 함”처럼 보이면 기대치가 과해질 수 있습니다. 더 정확한 표현은 다음입니다.

```text
계획형 AI 에이전트 파이프라인이 요구사항 분석, 구현, 검증, 수정 반복을 단계별로 수행합니다.
```

즉, “무제한 자유행동 에이전트”가 아니라 **통제된 app-building agent pipeline**으로 포지셔닝하는 것이 더 신뢰감 있습니다.

---

## 3. 실제 수정이 필요한 버그: 반복 실패 감지 후에도 재시도를 멈추지 않습니다

`appforge/runner.py`의 `_register_failure_signature()`에서 반복 실패를 감지하면 `REPEATED_FAILURE_LOOP`으로 바꾸고 이벤트도 emit합니다. 그런데 duplicate branch에서 `return False`를 하고 있어서, 호출부의 `stop_retrying`이 `False`가 됩니다. 결과적으로 “반복 실패 루프 감지”는 되지만, 실제로는 재시도를 멈추지 않습니다.

현재 구조상 의도는 멈추는 쪽으로 보입니다. 다음처럼 바꾸는 것이 맞습니다.

```python
if signature in seen:
    previous_code = str(failure.get("code") or "STAGE_FAILED")
    failure.update(
        {
            "code": "REPEATED_FAILURE_LOOP",
            "message": (
                f"Stage {stage.name} produced the same failing signature again "
                f"on attempt {attempt}."
            ),
            "action": (
                "Stop repeating the same repair path. Re-read the failed checks, change "
                "the implementation strategy, simplify the scope, or run the exact failing "
                "command locally before retrying."
            ),
            "previous_code": previous_code,
            "loop_signature": signature,
            "next_retry_mode": "regenerate",
            "repair_mode": "regenerate",
        }
    )
    self._emit(
        "loop_guard_triggered",
        stage=stage.name,
        attempt=attempt,
        failure=failure,
    )
    return True
```

그리고 테스트를 하나 추가하는 것이 좋습니다.

```text
같은 failure signature가 두 번째로 등록되면
_register_failure_signature()가 True를 반환하고,
호출부가 추가 retry를 중단하는지 확인
```

이건 제품 관점에서도 중요합니다. 자율 에이전트 서비스에서 같은 오류를 계속 반복하면 사용자는 “AI가 멍청하게 루프 돈다”고 느낍니다.

---

## 4. 진행 단계 UI와 백엔드 payload 이름이 맞지 않습니다

`StageTimeline.vue`는 다음 필드를 보고 있습니다.

```vue
stage.approval_required
stage.artifacts
```

그런데 백엔드의 stage record는 다음 필드를 내려줍니다.

```python
"produces": list(spec.produces)
"approval": bool(spec.approval)
```

프론트엔드 타입도 `JobStage`에 `produces?: string[]`, `approval?: boolean`로 되어 있습니다. 즉, 현재 timeline에서는 승인 표시와 산출물 버튼이 기대대로 안 보이거나, 타입 체크에서 문제가 날 가능성이 큽니다.

수정 방향은 둘 중 하나입니다.

첫 번째는 프론트엔드를 백엔드 payload에 맞추는 방식입니다.

```vue
<span v-else-if="stage.approval" class="stage-kind">APPROVAL</span>

<div
  v-if="(stage.artifacts?.length || stage.produces?.length)"
  class="stage-artifacts"
  aria-label="단계 산출물"
>
  <button
    v-for="artifact in (stage.artifacts || stage.produces)"
    :key="artifact"
    type="button"
    @click="emit('openArtifact', artifact)"
  >
    {{ artifact }}
  </button>
</div>
```

그리고 `frontend/src/types.ts`에는 다음을 추가하는 것이 좋습니다.

```ts
artifacts?: string[];
```

두 번째는 백엔드에서 `approval_required`, `artifacts`를 추가로 내려주는 방식입니다. 제품 API를 명확하게 하려면 백엔드 payload를 다음처럼 통일하는 것도 좋습니다.

```python
"produces": list(spec.produces),
"artifacts": [],
"approval": bool(spec.approval),
"approval_required": bool(spec.approval),
```

개인적으로는 **프론트엔드를 `approval`, `produces` 기준으로 맞추고, 완료된 실제 산출물은 `artifacts`로 따로 저장**하는 방식이 가장 깔끔합니다.

---

## 5. StageTimeline의 산출물 버튼 이벤트가 부모에서 연결되지 않았습니다

`StageTimeline.vue`는 `openArtifact` 이벤트를 emit합니다.

```ts
const emit = defineEmits<{
  openArtifact: [name: string];
}>();
```

그런데 `JobPanel.vue`에서는 이벤트 리스너 없이 이렇게만 사용합니다.

```vue
<StageTimeline :stages="props.job.stages" :active-stage="props.job.active_stage" />
```

그래서 timeline에 산출물 버튼이 보이더라도 클릭이 동작하지 않을 수 있습니다.

다음처럼 연결해야 합니다.

```vue
<StageTimeline
  :stages="props.job.stages"
  :active-stage="props.job.active_stage"
  @open-artifact="onOpenArtifact"
/>
```

이건 사용자 경험상 꽤 중요합니다. 자율 에이전트가 만든 중간 산출물을 바로 눌러 확인할 수 있어야 “에이전트가 일하고 있다”는 신뢰가 생깁니다.

---

## 6. `awaiting_approval` 상태 처리 기준이 프론트 내부에서 서로 다릅니다

`App.vue`에서는 `awaiting_approval`을 active 상태로 봅니다.

```ts
['queued', 'initializing', 'running', 'packaging', 'awaiting_approval']
```

그런데 `JobPanel.vue`에서는 active 상태에서 빠져 있습니다.

```ts
['queued', 'initializing', 'running', 'packaging']
```

이러면 승인 대기 중일 때 한쪽에서는 “아직 진행 중”으로 보고, 다른 쪽에서는 “활성 작업 아님”으로 볼 수 있습니다. 결과적으로 새 요청 버튼, 수정 요청, 재시도, 프리뷰 가능 여부가 애매하게 열릴 수 있습니다.

추천은 공통 helper를 만드는 것입니다.

```ts
export const ACTIVE_JOB_STATUSES = new Set([
  'queued',
  'initializing',
  'running',
  'packaging',
  'awaiting_approval',
]);

export function isActiveJobStatus(status: string) {
  return ACTIVE_JOB_STATUSES.has(status);
}

export function isTerminalJobStatus(status: string) {
  return ['completed', 'failed', 'cancelled'].includes(status);
}
```

그리고 `App.vue`, `JobPanel.vue`가 같은 기준을 쓰게 하세요.

---

## 7. 보안 기본값은 더 보수적으로 잡는 것이 좋습니다

이 서비스는 “AI가 앱을 만들고 명령을 실행하는” 성격입니다. 따라서 일반 코드 생성기보다 보안 기본값이 더 중요합니다.

현재 `WebConfig`에서 `allow_network` 기본값이 `True`입니다.

```python
allow_network: bool = True
```

앱 빌더 입장에서는 편하지만, 자율 에이전트 서비스 기본값으로는 다소 공격적입니다. 추천은 기본값을 `False`로 두고, 사용자가 명시적으로 “패키지 설치/네트워크 허용”을 켜게 하는 것입니다.

```python
allow_network: bool = False
```

또 하나 중요한 부분은 `appforge/tooling/command.py`입니다. `run_command()`가 subprocess 실행 시 전체 host 환경변수를 복사합니다.

```python
merged_env = os.environ.copy()
```

이러면 생성된 앱의 테스트/빌드 스크립트가 호스트의 API 키, 토큰, 내부 환경변수에 접근할 가능성이 생깁니다. stdout/stderr redaction은 되어 있지만, 애초에 프로세스 환경으로 비밀값이 넘어가는 것이 위험합니다.

추천은 allowlist 방식입니다.

```python
SAFE_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
}

merged_env = {
    key: value
    for key, value in os.environ.items()
    if key in SAFE_ENV_KEYS
}
```

그리고 필요한 값만 명시적으로 추가하세요.

이건 제품 신뢰도에 큰 영향을 줍니다. “AI가 코드를 만들고 실행한다”는 서비스는 **secret isolation**을 강하게 보여줘야 합니다.

---

## 8. 프리뷰 sandbox/CSP도 더 엄격하게 가져가는 것이 좋습니다

현재 프리뷰 iframe은 다음 sandbox를 씁니다.

```html
sandbox="allow-scripts allow-forms allow-popups"
```

그리고 `/preview/` CSP에는 다음이 포함되어 있습니다.

```text
script-src 'self' 'unsafe-inline' 'unsafe-eval'
connect-src 'self'
```

생성 앱 프리뷰에는 JS 실행이 필요할 수 있으므로 완전히 막을 수는 없습니다. 그래도 기본값으로 `allow-popups`는 빼는 것을 추천합니다. 폼 제출도 꼭 필요한 경우에만 켜는 편이 낫습니다.

기본은 이렇게 시작하는 것이 안전합니다.

```html
sandbox="allow-scripts"
```

그리고 UI에서 옵션을 분리할 수 있습니다.

```text
고급 프리뷰 옵션
[ ] 폼 제출 허용
[ ] 팝업 허용
[ ] 외부 네트워크 요청 허용
```

현재 제품 설명에 “검증된 소스 ZIP”이라는 표현이 있으므로, 프리뷰 보안도 사용자가 믿을 수 있게 설계해야 합니다.

---

## 9. LLM bridge의 파일 생성량 제한을 추가하는 것이 좋습니다

`appforge/drivers.py`의 `_write_bridge_files()`는 LLM bridge response의 `files`를 순회하며 파일을 씁니다. 경로 안전성 검사는 있지만, 파일 개수/크기/총량 제한은 뚜렷하게 보이지 않습니다.

자율 생성 서비스에서는 모델이 실수로 너무 많은 파일이나 너무 큰 파일을 만들 수 있습니다. 다음 제한을 추천합니다.

```python
MAX_BRIDGE_FILES = 120
MAX_BRIDGE_FILE_BYTES = 512_000
MAX_BRIDGE_TOTAL_BYTES = 5_000_000
```

예시 방향입니다.

```python
def _write_bridge_files(layout: ProjectLayout, files: Any) -> list[str]:
    payloads = _iter_file_payloads(files)

    if len(payloads) > MAX_BRIDGE_FILES:
        raise DriverError(f"Too many files: {len(payloads)} > {MAX_BRIDGE_FILES}")

    total = 0
    changed: list[str] = []

    for relative_path, content in payloads:
        size = len(content.encode("utf-8"))
        if size > MAX_BRIDGE_FILE_BYTES:
            raise DriverError(f"File too large: {relative_path}")

        total += size
        if total > MAX_BRIDGE_TOTAL_BYTES:
            raise DriverError("Generated file payload is too large")

        if _is_bridge_managed_output_path(relative_path):
            continue

        path = _safe_workspace_path(layout, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        changed.append(str(path.relative_to(layout.root)))

    return changed
```

이건 안정성뿐 아니라 비용/성능 관리에도 도움이 됩니다.

---

## 10. 문서와 실제 기본값이 일부 맞지 않습니다

문서 정합성도 고치는 것이 좋습니다. 제품 신뢰도에 바로 영향을 줍니다.

확인된 항목은 다음입니다.

`README.md`, `README.ko.md`에서 `AGENT_GUIDE.md`를 링크하지만 실제 파일은 없고, 루트에는 `AGENTS.md`가 있습니다. 링크를 바꾸거나 `AGENT_GUIDE.md`를 추가해야 합니다.

`README.ko.md`에는 `APPFORGE_DRIVER` 기본값이 `auto`처럼 설명되어 있지만, 코드상 기본값은 `llm-bridge-agent`입니다.

```python
driver=os.environ.get("APPFORGE_DRIVER", "llm-bridge-agent")
```

`README.ko.md`에는 `APPFORGE_LLM_BRIDGE_AUTOSTART`가 나오고, `docs/WEB_APP.md`나 `build.sh`에는 `APPFORGE_START_LLM_BRIDGE`가 나옵니다. 실제 자동 시작 로직은 `APPFORGE_LLM_BRIDGE_AUTOSTART`와 `APPFORGE_SKIP_LLM_BRIDGE`도 사용합니다. 이 부분은 “웹 서버 내 자동 시작”과 “build.sh 런처에서 시작”을 구분해서 설명해야 혼란이 줄어듭니다.

`build.sh` 도움말에는 아직 `v4 web UI`라고 되어 있습니다.

```text
Prepare and launch the local AppForge-LLM v4 web UI.
```

현재 버전은 v6이므로 수정이 필요합니다.

---

## 11. 사용하지 않는 프론트 컴포넌트 정리도 추천합니다

`WorkspaceBrowser.vue`, `ArtifactBrowser.vue`가 존재하지만 현재 `JobPanel.vue`에서 직접 비슷한 기능을 구현하고 있는 것으로 보입니다. 이런 중복은 시간이 지나면 한쪽만 고쳐져 UI가 어긋나기 쉽습니다.

둘 중 하나를 선택하는 것이 좋습니다.

1. `JobPanel.vue`의 inline 구현을 컴포넌트로 분리해서 `WorkspaceBrowser`, `ArtifactBrowser`를 실제 사용한다.
2. 사용하지 않을 컴포넌트는 제거한다.

제품 개발 속도를 생각하면 1번이 낫습니다. “워크스페이스 보기”, “아티팩트 보기”는 앞으로 계속 중요해질 기능입니다.

---

## 추천 우선순위

**P0 — 바로 수정 권장**

1. `runner.py` 반복 실패 루프 감지 후 `return True`로 변경
2. `StageTimeline.vue`의 `approval_required/artifacts` mismatch 수정
3. `JobPanel.vue`에서 `@open-artifact="onOpenArtifact"` 연결
4. `awaiting_approval` active 상태 기준을 공통 helper로 통일

**P1 — 서비스 신뢰도/안전성**

1. `allow_network` 기본값을 보수적으로 변경하거나 UI에서 명시적 허용
2. subprocess 실행 시 전체 환경변수 전달 금지
3. 프리뷰 iframe sandbox/CSP 강화
4. LLM bridge 파일 개수/크기/총량 제한 추가

**P2 — 제품화/브랜딩**

1. Hero에서 `V6`, `Vite`, `Vue` 같은 내부 기술명을 뒤로 빼기
2. “프롬프트 하나로 앱 제작” 메시지를 전면화
3. `README.ko.md`, `docs/WEB_APP.md`, `build.sh` 버전/환경변수 설명 정리
4. `AGENT_GUIDE.md` 누락 링크 수정

---

## 결론

이 앱은 이미 “프롬프트 기반 자율 앱 제작 서비스”의 뼈대가 있습니다. 특히 **큐, 승인 모드, 수정 요청, 아티팩트/코드 뷰어, ZIP 다운로드, LLM bridge 기반 agent loop**는 방향이 좋습니다.

다만 지금 상태로는 사용자가 처음 봤을 때 “AI 앱 제작 서비스”보다 “개발자용 로컬 파이프라인 UI”로 읽힐 수 있습니다. 그래서 첫 번째 개선은 **카피/브랜딩 정리**, 두 번째는 **진행 단계 UI 버그와 반복 실패 루프 버그 수정**, 세 번째는 **자율 실행에 맞는 보안 기본값 강화**로 잡는 것을 추천합니다.
