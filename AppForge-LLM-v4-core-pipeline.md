# AppForge-LLM v4 · 코어 파이프라인 흐름

요청이 들어온 순간부터 ZIP 다운로드까지의 실행 흐름. 모든 결정·실행·검증은 서버가 소유하며,
브라우저는 상태를 폴링만 한다.

## 0. 전체 구조

```
브라우저(Vue SPA) ─/api→ FastAPI(appforge/web.py)
                           ├─ JobManager(web_jobs.py)       : 잡 1개만 실행, 상태 영속
                           ├─ PipelineRunner(runner.py)     : 단계 순차 실행·검증·체크포인트
                           └─ LLMBridgeDriver(drivers.py) ─HTTP→ 로컬 Bun 브릿지(llm_bridge/)
                                                          └─ coco @opencode-ai/llm 엔진 → 외부 LLM(DeepSeek 등)
```

## 1. 요청 → 라우팅 → 잡 생성

- 입력 → `JobManager.create_job`(`web_jobs.py:312`). 입력 길이 제한 존재.
- `auto_select_pipeline`(`pipelines.py:54`): 키워드 점수만으로 파이프라인 선택("웹앱" → `web-app`). 복잡도는 보지 않음.
- 백그라운드 스레드에서 `_run_job`(`web_jobs.py:473`) 시작.

## 2. 시스템 사전 단계 (2개)

- **preflight** — 브릿지 ping + 활성 프로바이더/모델 확인. 미준비 시 `AGENT_NOT_AVAILABLE`로 즉시 실패.
- **project_setup** — `projects/<slug>-<id>/` 격리 작업공간 + 체크포인트 디렉토리 생성.

## 3. 파이프라인 13단계 (각 = LLM 호출 1회 + 검증)

`PipelineRunner._run_stage`(`runner.py:253`)가 단계별 최대 3회 시도하는 루프:

1. `build_stage_prompt` — 스킬 + 이전 산출물 + 메모리 장부 + 아티팩트 스키마 결합 (`prompting.py`)
2. `LLMBridgeDriver.run` — 브릿지 `/generate` → 외부 LLM → JSON envelope 반환
3. `_apply_bridge_envelope` — `files` 작성 + `artifacts/<name>.json` 작성 + 스키마 검증
4. 검증 4종:
   - `validate_stage_result` (`.appforge/stage-result.json` 스키마)
   - `validate_stage_artifacts` (각 artifact 스키마)
   - `run_declared_gates` (`run_tests` / `run_build` / `secret_scan` … 실제 명령 실행)
   - `review_stage` (결정적 리뷰 — critical 발견 시 실패)
5. 실패 시 `failure_signature` 저장 → 같은 서명 반복 시 `REPEATED_FAILURE_LOOP` 정지.
6. 통과 시 체크포인트 기록 → 다음 단계.

### 13개 단계와 산출물 (`web-app.yaml`)

| # | 단계 | 산출물 | 게이트 | approval |
|---|---|---|---|---|
| 1 | intake | product_brief | — | |
| 2 | specification | requirements_spec | — | |
| 3 | workflow_design * | workflow_spec | — | |
| 4 | memory_engineering * | memory_spec | — | |
| 5 | loop_engineering * | loop_spec | — | |
| 6 | architecture | architecture_spec | — | ✓ |
| 7 | experience | experience_spec | — | ✓ |
| 8 | implementation | implementation_report | detect_stack | |
| 9 | verification | verification_report | run_tests, run_build | |
| 10 | security | security_report | secret_scan | |
| 11 | release | release_report | run_build, secret_scan, release_readiness | ✓ |
| 12 | handoff | handoff_report | archive_workspace (ZIP) | |
| 13 | download_package (시스템) | — | ZIP 무결성 검증 | |

\* 3·4·5번 = v4 엔지니어링 스파인(상태/메모리/루프 설계용).

## 4. 완료 → 인계

- **handoff** — `archive_workspace`가 `.appforge/reports/<name>-source.zip` 생성 (`.appforge/`, `.git/`, `.env`, 의존성/캐시 디렉토리 제외).
- **download_package** — ZIP을 `zipfile.testzip()`으로 검증 후에만 다운로드 URL 노출 (`web_jobs.py:737`).

## 비용 구조 (1회 요청)

- **LLM 호출**: 13단계 × (1회 + 재시도 시 최대 2회) = 통상 13~20회 외부 LLM 호출.
- **실제 명령 실행**: verification(build/test), security(시크릿 스캔), release(빌드)에서 프로젝트 코드 실행.
- 복잡도에 무관하게 **모든 요청이 동일 13단계**를 통과.

## 쟁점

흐름 자체(격리 작업공간 → 단계별 검증 → 체크포인트 → ZIP)는 타당하게 짜여 있으나,
3·4·5번 스파인(workflow/memory/loop engineering)이 상태ful 백엔드용 개념임에도 단순 클라이언트
앱에도 일괄 적용된다. 복잡한 앱(auth/결제/데이터/동시성)에서는 제값을 하지만, 단순 웹앱에서는
관객용 산출물로 전락할 수 있다.
