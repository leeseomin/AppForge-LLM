검토 결과: **현재 흐름은 “복잡한 production 앱” 기준으로는 꽤 잘 설계됐지만, 모든 요청에 항상 필요한 흐름은 아닙니다.** 특히 `workflow_design → memory_engineering → loop_engineering` 3단계는 v4의 핵심 의도는 이해되지만, 단순 웹앱·랜딩페이지·정적 MVP에는 비용 대비 효과가 낮습니다. 첨부 문서도 동일하게 “복잡도와 무관하게 동일 15단계”가 쟁점이라고 적고 있습니다. 

제가 ZIP 내부 실제 코드까지 확인했고, Python 테스트도 실행했습니다. 결과는 `python -m pytest -q` 기준 **44개 테스트 전부 통과**였습니다.

## 1. 실제 코드 기준으로 문서의 흐름은 맞는가?

**대체로 맞습니다.** 문서의 “브라우저는 상태를 폴링하고, FastAPI 서버가 JobManager → PipelineRunner → LLMBridgeDriver를 통해 단계 실행을 소유한다”는 구조는 실제 코드와 일치합니다. 문서상 UI는 web-app 기준으로 시스템 3단계와 파이프라인 12단계를 보여주며, LLM 호출은 파이프라인 단계에서만 발생합니다. 

실제 코드 흐름은 이렇습니다.

`appforge/web_jobs.py`

* `create_job()`에서 입력 길이 검증, `auto_select_pipeline()`, 잡 생성, 단일 active job 제한을 수행합니다. 실제 위치는 `web_jobs.py:312-383`.
* `_run_job()`에서 `preflight → project_setup → PipelineRunner.run() → download_package`를 실행합니다. 실제 위치는 `web_jobs.py:473-637`.
* `_initial_stages()`가 UI 단계 배열을 만듭니다. 먼저 `preflight`, `project_setup`를 넣고, 선택된 pipeline의 stages를 모두 넣은 뒤 마지막에 `download_package`를 붙입니다. 실제 위치는 `web_jobs.py:892-935`.

`appforge/runner.py`

* `PipelineRunner.run()`은 체크포인트 기준 다음 단계를 찾고, 순차 실행합니다. 실제 위치는 `runner.py:76-251`.
* `_run_stage()`는 단계별 최대 3회 시도, 프롬프트 생성, LLM driver 실행, 산출물 검증, gate 실행, review, 체크포인트 저장, 반복 실패 감지를 수행합니다. 문서의 설명과 맞습니다. 

다만 중요한 차이가 하나 있습니다. **표의 approval 체크는 웹앱에서는 사실상 작동하지 않습니다.** web job은 `initialize_project(..., mode="autonomous")`로 만들고, `PipelineRunner(..., auto_approve=True)`로 실행합니다. 따라서 `architecture`, `experience`, `release`에 `approval: true`가 있어도 웹 UI에서는 사람 승인을 기다리지 않습니다. approval은 CLI의 guided/pause 모드에서 의미가 있고, 현재 웹 UX에서는 “표시용 설계 의도”에 가깝습니다.

## 2. “모두 필수인가?”에 대한 결론

아닙니다. **필수 단계와 조건부 단계가 섞여 있습니다.**

| 단계                 |                현재 판단 | 이유                                                                                                                    |
| ------------------ | -------------------: | --------------------------------------------------------------------------------------------------------------------- |
| preflight          |                   필수 | LLM 브릿지/프로바이더 준비 안 된 상태에서 긴 파이프라인을 시작하면 낭비가 큽니다.                                                                      |
| project_setup      |                   필수 | 격리 작업공간, `.appforge`, 체크포인트 구조가 전체 실행 안정성의 기반입니다.                                                                     |
| intake             |                거의 필수 | 사용자 요청을 product brief로 고정하는 단계는 필요합니다. 단순 앱에서는 specification과 합쳐도 됩니다.                                                |
| specification      |                   필수 | 구현·검증 기준이 됩니다. 다만 단순 앱에서는 intake와 통합 가능.                                                                              |
| workflow_design    |                  조건부 | 상태 전이, 재시도, 외부 효과, auth, 결제, 백그라운드 작업이 있으면 중요합니다. 정적 페이지에는 과합니다.                                                      |
| memory_engineering |                  조건부 | DB, 세션, 캐시, 파일 저장, 감사 로그, 재시작 복구가 있으면 중요합니다. stateless UI에는 대개 과합니다.                                                  |
| loop_engineering   |                  조건부 | polling, retry, worker, queue, reconciliation, human approval loop가 있으면 중요합니다. 단순 CRUD도 가볍게는 필요하지만 별도 LLM 단계까지는 과합니다. |
| architecture       |              필수에 가까움 | 기술 구조 결정은 필요합니다. 단, 단순 앱에서는 specification 안의 architecture-lite로 충분할 수 있습니다.                                           |
| experience         |        웹앱에서는 필수에 가까움 | UI/UX 앱이면 화면·상태·접근성 설계가 필요합니다. API/CLI에는 별도 UX 단계가 필요 없습니다.                                                           |
| implementation     |                   필수 | 실제 산출물 생성 단계입니다.                                                                                                      |
| verification       |                   필수 | test/build를 실제로 실행하는 유일한 강한 품질 단계입니다.                                                                                 |
| security           |   최소 secret scan은 필수 | 별도 threat model 단계는 앱 복잡도에 따라 조건부입니다.                                                                                 |
| release            |            조건부/축소 가능 | 현재 `run_build`, `secret_scan`을 다시 실행하므로 verification/security와 중복됩니다. 배포용이면 필요하지만 ZIP 전달용이면 축소 가능.                    |
| handoff            | 필요하지만 LLM 단계일 필요는 약함 | README/quickstart/ZIP 준비는 필요합니다. 상당 부분은 템플릿/시스템 단계로 자동화 가능합니다.                                                        |
| download_package   |                   필수 | ZIP 무결성 확인 후 URL 노출은 좋은 안전장치입니다.                                                                                      |

## 3. 실제 코드에서 확인한 핵심 문제점

가장 큰 문제는 **복잡도 라우팅이 약하다**는 점입니다. `auto_select_pipeline()`은 키워드 점수와 몇 가지 hard rule로 pipeline을 고릅니다. 예를 들어 “SaaS”, “mobile”, “prototype” 같은 단어는 강하게 반영하지만, “간단한 정적 웹앱인지, 인증/결제/DB/동시성/외부 연동이 있는 앱인지” 같은 복잡도는 판단하지 않습니다. 실제 위치는 `appforge/pipelines.py:54-84`입니다. 첨부 문서도 `auto_select_pipeline`이 키워드 점수 기반이고 복잡도를 보지 않는다고 설명합니다. 

두 번째 문제는 **web-app뿐 아니라 대부분의 내장 pipeline에 v4 spine이 일괄 삽입되어 있다는 점**입니다. 실제 pipeline 파일을 보면 `web-app`, `prototype`, `bugfix`, `feature`, `cli-tool`, `automation` 등 대부분이 `workflow_design`, `memory_engineering`, `loop_engineering`을 포함합니다. `docs/V4_ENGINEERING.md`에도 모든 built-in pipeline이 v4 spine을 포함한다고 되어 있고, 실제 파일들도 그렇게 되어 있습니다. 이건 복잡한 시스템에는 좋지만, 단순 산출물 생성기에는 호출 수·지연·비용을 키웁니다.

세 번째 문제는 **LLMBridgeDriver의 실제 실행 방식이 “코딩 에이전트”라기보다는 “JSON envelope 생성기”라는 점**입니다. `drivers.py`의 bridge prompt는 모델에게 “명령 실행이나 직접 파일 편집은 할 수 없고, 만들 파일을 `files`에 넣어라”고 요구합니다. 그 뒤 runner가 파일과 artifact를 씁니다. 즉 implementation 단계의 `tools: run_command, install_dependencies, read_text...`는 프롬프트에는 들어가지만, 현재 bridge 경로에서는 모델이 실제로 도구를 호출하는 구조가 아닙니다. 실제 검증은 stage 이후 gate에서만 일어납니다. 이 구조는 안전하고 단순하지만, 복잡한 앱 구현에는 “한 번의 JSON 응답으로 전체 코드를 생성”해야 해서 취약합니다.

네 번째 문제는 **release 단계가 verification/security와 중복됩니다.** web-app pipeline에서 verification은 `run_tests`, `run_build`를 required로 실행하고, security는 `secret_scan`을 required로 실행합니다. 그런데 release가 다시 `run_build`, `secret_scan`, `release_readiness`를 required로 실행합니다. 첨부 문서의 표도 release 단계가 `run_build`, `secret_scan`, `release_readiness`를 가진다고 설명합니다.  중복 자체가 항상 나쁜 것은 아니지만, security 이후 코드 변경이 없다면 비용 대비 효용이 낮습니다.

다섯 번째 문제는 **handoff 산출물과 다운로드 ZIP의 포함 범위가 어긋날 수 있습니다.** `ArchiveWorkspaceTool`은 `.appforge/` 전체를 ZIP에서 제외합니다. 그래서 `.appforge/artifacts/handoff_report.json`, verification/security/release report도 ZIP에 포함되지 않습니다. source archive로는 안전하지만, “검증 증거와 인계 자료까지 전달”한다는 handoff 의도와는 충돌합니다. 해결하려면 handoff 단계가 `HANDOFF.md`, `docs/verification.md`, `docs/architecture.md`처럼 sanitize된 파일을 일반 소스 트리에 생성해야 합니다.

## 4. 그래도 잘 설계된 부분

현재 구조의 장점도 분명합니다.

첫째, **실행 소유권이 서버에 있고 브라우저는 폴링만 한다**는 구조는 안정적입니다. 새로고침해도 job state를 다시 불러올 수 있고, 하나의 active job만 허용해 동시 실행 충돌을 막습니다.

둘째, **체크포인트/재시도/반복 실패 감지**가 잘 들어가 있습니다. `runner.py`는 매 attempt마다 stale `.appforge/stage-result.json`을 삭제하고, 같은 failure signature가 반복되면 `REPEATED_FAILURE_LOOP`로 멈춥니다. 이건 장시간 자동 생성 시스템에서 꽤 중요한 안전장치입니다.

셋째, **스키마 기반 artifact 검증**은 좋습니다. 각 단계가 산출해야 하는 JSON artifact가 명확하고, `validate_stage_artifacts()`가 schema validation을 통과해야 다음 단계로 갑니다. 덕분에 LLM이 말로만 “완료”했다고 해도 구조화 산출물이 없으면 실패합니다.

넷째, **download_package의 ZIP 무결성 검증은 좋은 마무리 단계**입니다. `_validate_archive()`가 `zipfile.is_zipfile()`과 `ZipFile.testzip()`을 사용해 손상 여부를 확인한 뒤 다운로드 URL을 노출합니다. 문서의 설명과도 일치합니다. 

## 5. 추천 구조

현재 구조를 유지하되, pipeline을 **복잡도별 3단계 모드**로 나누는 게 가장 좋습니다.

### A. simple web-app / landing / static MVP

권장 흐름:

1. preflight
2. project_setup
3. brief_spec — intake + specification + UX-lite
4. architecture_lite — 기술 선택과 파일 구조
5. implementation
6. verification_security — build/test/secret_scan
7. handoff_download — README, ZIP, 무결성 확인

이 경우 별도 `workflow_design`, `memory_engineering`, `loop_engineering`, `release`는 필요 없습니다. 15단계를 7단계 안팎으로 줄일 수 있습니다.

### B. normal production web-app

권장 흐름:

1. preflight
2. project_setup
3. intake
4. specification
5. architecture_experience
6. implementation
7. verification
8. security
9. release_handoff
10. download_package

여기서는 workflow/memory/loop를 별도 단계가 아니라 `specification` 또는 `architecture_experience` artifact 안의 섹션으로 넣는 게 낫습니다.

### C. complex stateful app / SaaS / automation / payments / auth / jobs

현재 v4 spine을 유지할 가치가 있습니다.

`intake → specification → workflow_design → memory_engineering → loop_engineering → architecture → experience → implementation → verification → security → release → handoff`

이 구조는 인증, 결제, 멀티테넌시, 외부 API, 재시도, 작업 큐, 감사 로그, 데이터 마이그레이션이 있는 앱에는 적절합니다.

## 6. 코드 차원에서 바꾸면 좋은 부분

우선 `auto_select_pipeline()`에 complexity scoring을 넣어야 합니다. 현재는 pipeline 종류만 고르고, 같은 web-app 안에서 simple/production/complex를 나누지 못합니다. 최소한 아래 신호는 점수화하는 게 좋습니다.

`auth`, `login`, `payment`, `subscription`, `database`, `admin`, `multi-user`, `realtime`, `websocket`, `upload`, `background job`, `queue`, `external API`, `audit`, `role`, `permission`, `multi-tenant`, `데이터 저장`, `로그인`, `결제`, `관리자`, `실시간`, `업로드`.

그 다음 pipeline 정의를 나누는 방식이 깔끔합니다.

* `web-app-simple.yaml`
* `web-app.yaml`
* `web-app-complex.yaml` 또는 복잡하면 `fullstack-saas.yaml`로 라우팅

또는 runner가 stage별 `when` 조건을 지원하게 할 수 있습니다. 예를 들어 pipeline schema에 이런 필드를 추가합니다.

```yaml
- name: memory_engineering
  when:
    any_flags:
      - persistence
      - auth
      - sessions
      - cache
      - audit_log
```

하지만 이 방식은 runner, schema, UI progress 계산, 테스트를 모두 바꿔야 합니다. 당장은 pipeline 파일을 나누는 편이 더 안전합니다.

## 7. 제 최종 판단

**현재 v4 pipeline은 “안전하고 통제된 production generator”로는 적절하지만, “모든 앱 생성 요청의 기본값”으로는 무겁습니다.**
특히 web-app 기준 5·6·7번 spine은 복잡한 앱에서는 품질을 올리지만, 단순 앱에서는 비용과 지연을 늘리고 관객용 문서가 될 가능성이 큽니다.

그래서 결론은 다음과 같습니다.

**필수로 남길 것:** preflight, project_setup, specification 계열, implementation, verification, 최소 security, archive/download.
**조건부로 돌릴 것:** workflow_design, memory_engineering, loop_engineering, full release review.
**웹 UI에서 정리할 것:** approval 표시는 실제 웹 실행에서 pause가 없으므로 제거하거나, guided approval 기능을 실제로 구현해야 합니다.
**가장 먼저 고칠 것:** `auto_select_pipeline()`을 단순 키워드 라우터에서 complexity-aware router로 바꾸고, `web-app-simple` pipeline을 추가하는 것입니다.
