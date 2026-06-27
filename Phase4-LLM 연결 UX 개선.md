# AppForge-LLM v4 · Phase 4 — LLM 연결 UX 개선

> **상태**: 계획 승인 대기 · **결정점 2개 미해결** (아래 "F. 결정 대기" 참조)
> **목표**: OpenRouter 338개 모델 등 대규모 카탈로그 환경에서 웹앱 모델 선택 UX 3종 개선
> **선행 완료**: Phase 1(CLI auth + models.dev catalog), Phase 2(GUI 빠른 연결), Phase 3(OAuth)

## 0. 배경 — 측정된 현재 상태

| 항목 | 측정값 | 문제 |
|---|---|---|
| `GET /providers` 응답 크기 | **285 KB** (모든 프로바이더의 모델 포함) | 초기 로딩 병목 |
| `GET /providers/models` 응답 크기 | **253 KB** | `allModels()` — 사용처 없음에도 비대 |
| `GET /providers/openrouter/models` | **20 KB** (단일 프로바이더) | lazy loading에 적합 |
| 모델 선택 UI 3곳 | HTML `<datalist>` (접두사 매칭만) | "claude" 검색 → `anthropic/claude-3.5-sonnet` 매칭 안 됨 |
| model-chips (고급 설정) | 338개 버튼 한 영역 무한 나열 | OpenRouter 펼치기 시 UI 폭주 |
| `getProviderModels()` | `frontend/src/api.ts:81`에 **이미 존재** | lazy loading plumbing 준비됨 |

## 1. 개선 항목 3종

### A. 백엔드 — `/providers` 응답 경량화 (SSOT)

**브릿지 (`llm_bridge/src/`)**
- `types.ts:55`: `ProviderStatus.models` → optional (`models?: ProviderModel[]`)
- `registry.ts` `statusOf()`: `models` 필드 **기본 생략**. 빈 배열 `[]` 반환 (undefined보다 타입 안전 — 기존 `provider.models.map(...)` 코드가 빈 배열에서도 동작)
- `server.ts` `listProviders()`: 쿼리 파라미터 `?include_models=true` 파싱 → 옵트인 시에만 전체 모델 주입 (후방 호환)
- `server.ts` `allModels()`: **폐기** (사용처 없음, `/providers/{id}/models`가 SSOT) — 결정점 F-1 참조

**Python (`appforge/`)**
- `llm_auth.py`: 이미 `provider_models` 별도 호출 사용 → 영향 없음
- `web.py`: `/api/llm/providers` 프록시 → 응답 그대로 전달 → 영향 없음

**테스트 호환성**
- `llm_bridge/test/server.test.ts`: `models` 없이 upsert만 검증 → 영향 없음
- `llm_bridge/test/registry.test.ts`: `statusOf` 검증 → 빈 배열에서도 동작, 마이너 수정
- `tests/test_web.py:377`: `list_providers` mock에 `models` 포함 → 빈 배열로 변경해도 health 체크는 `default_model` 사용 → OK
- `tests/test_llm_auth.py`: `cmd_login`은 `provider_models` 별도 호출 → OK

### B. 프론트엔드 — fuzzy 검색 컴포넌트 (외부 의존성 0)

**신규 파일: `frontend/src/components/ModelSelect.vue`**
- Vue 3 + TypeScript 순수 구현 (외부 라이브러리 0 — 프로젝트 스타일 준수, KISS)
- 입력 필드 + 필터링된 드롭다운 팝오버
- **fuzzy 매칭**: 소문자 변환 후 `id`와 `name` 양쪽에서 `includes` 매칭, 접두사 매칭에 가산점 (coco questionary autocomplete와 동등)
- 키보드 탐색 지원 (↑↓ Enter Esc)
- Props: `models: ProviderModel[]`, `modelValue: string`, `placeholder`, `loading?`
- Emits: `update:modelValue`
- 338개 모델에서 "claude" 입력 → `anthropic/claude-3.5-sonnet` 즉시 필터링 (datalist 한계 해결)

**`ProviderSettings.vue` 수정 — datalist 3곳 → `<ModelSelect>` 교체**
- quick-connect (425행 근처): `qcModelOptions` 바인딩
- active-block (532행 근처): `activeModelOptions` 바인딩
- advanced (603행 근처): `provider.models` 바인딩

### C. lazy loading 통합 (B와 동시 적용)

**`ProviderSettings.vue`**
- `/providers` 응답의 `models`가 빈 배열 → provider 선택 시 `getProviderModels(id)` 호출해 채움
- 캐시: `loadedModels: Map<string, ProviderModel[]>` (provider id → models)
- provider 변경 시에만 fetch (initial `/providers` 호출 시 models 빈 배열)
- 로딩 중: `ModelSelect`에 `loading` prop 전달 → 스피너/플레이스홀더
- 실패 시: 빈 배열 + "모델 목록 로딩 실패" 메시지 + 수동 입력 허용 (기존 datalist 열화 아님)

### D. model-chips "더 보기" 토글

**`ProviderSettings.vue`**
- `modelSuggestions(provider)` → 처음 10개만 반환하는 computed로 분리 (`modelSuggestionsLimited`)
- "더 보기 (N개)" 버튼 추가 → 클릭 시 전체 펼침 (per-provider `expandedChips: Set<string>`)
- 338개 칩이 한 영역에 쏟아지는 현상 방지

**`styles.css`**
- `.model-chips`에 `max-height` + `overflow-y: auto` 추가 (펼침 시 스크롤)
- "더 보기" 버튼 스타일
- `ModelSelect` 팝오버 스타일 (드롭다운, 하이라이트, 키보드 포커스)

## 2. 파일 변경 목록

| 파일 | 변경 유형 | 설명 |
|---|---|---|
| `llm_bridge/src/types.ts` | 수정 | `ProviderStatus.models` optional화 |
| `llm_bridge/src/registry.ts` | 수정 | `statusOf` — models 기본 빈 배열, `?include_models` 지원 |
| `llm_bridge/src/server.ts` | 수정 | `listProviders` 쿼리 파라미터 파싱, `allModels` 폐기(F-1 A 선택 시) |
| `llm_bridge/test/registry.test.ts` | 수정 | models 생략 케이스 추가 |
| `frontend/src/components/ModelSelect.vue` | **신규** | fuzzy 검색 컴포넌트 |
| `frontend/src/components/ProviderSettings.vue` | 수정 | datalist 3곳 → ModelSelect, lazy loading, model-chips 토글 |
| `frontend/src/styles.css` | 수정 | model-chips 스크롤, "더 보기" 버튼, ModelSelect 팝오버 |
| `tests/test_web.py` | 수정 (선택) | `list_providers` mock에서 models 제거 |

## 3. 검증 계획

| 항목 | 명령 | 기대 결과 |
|---|---|---|
| bridge typecheck | `cd llm_bridge && bun run typecheck` | 통과 |
| bridge test | `cd llm_bridge && bun test` | 기존 6 + 신규 케이스 통과 |
| 프론트엔드 빌드 | `cd frontend && npm run build` | `vue-tsc --noEmit && vite build` 성공 |
| ruff | `.venv/bin/ruff check appforge/ tests/` | All checks passed |
| pytest | `.venv/bin/pytest` | 기존 65 + 신규 테스트 통과 |
| E2E 응답 크기 | `curl -s http://127.0.0.1:8788/providers \| wc -c` | 285 KB → 수 KB로 감소 |
| E2E lazy load | `curl -s http://127.0.0.1:8788/providers/openrouter/models \| wc -c` | 20 KB로 개별 로딩 확인 |
| E2E fuzzy 검색 | 브라우저에서 "claude" 입력 | `anthropic/claude-*` 모델 즉시 필터링 |

## 4. AGENT_GUIDE 준수 — 파이프라인 진입

이 작업은 기존 레포 변경이므로 `AppForge-LLM-v4/` 하위에서 feature 파이프라인으로 진입:

```bash
cd AppForge-LLM-v4
appforge new "LLM 연결 UX 3종 개선 — /providers 경량화 + fuzzy 모델 검색 + model-chips 토글" \
  --target . --pipeline feature --mode autonomous
```

단계별 산출물·게이트·체크포인트 루프 준수 (AGENT_GUIDE.md:36-43).

## 5. 트레이드오프

- **lazy loading 추가 API 호출**: provider 선택 시 1회 추가 fetch (20 KB). 초기 285 KB 로딩 제거로 상쇄. 사용자가 프로바이더 전환할 때마다 발생하지 않도록 메모리 캐시 유지.
- **ModelSelect 신규 컴포넌트**: 3곳 재사용으로 DRY. 약 200줄 추가 but 외부 의존성 0.
- **`/providers/models` 폐기(F-1 A)**: 후방 호환 깨짐. 단, `api.ts`에 대응 함수 없음 → 실사용처 0. 위험 최소.
- **`/providers?include_models=true` 옵트인**: 기존 동작 필요 시 사용. 후방 호환 보장.

## 6. 핵심 한계 (본 Phase 범위 외)

현재 구조는 파이프라인 12단계가 **모두 동일한 활성 모델** 사용. 단계별로 다른 모델 지정(예: intake는 빠른 모델, implementation은 강한 모델)은 별도 설계 변경 필요 → Phase 5 후보.

## F. 결정 대기 (실행 전 확인 필요)

### F-1. `/providers/models` 엔드포인트 처리
| 옵션 | 설명 |
|---|---|
| **A) 폐기 (추천)** | 사용처 없음, `/providers/{id}/models`가 SSOT. 깔끔하지만 후방 호환 깨짐 (위험 최소) |
| B) 경량화만 | id/name만 반환. 후방 호환 유지 but 253KB → 수십 KB로만 감소 |

### F-2. lazy loading 캐시 지속성
| 옵션 | 설명 |
|---|---|
| **A) 메모리 캐시만 (추천)** | `Map`으로 보관. 설정 모달 닫으면 해제. 단순, stale 없음 |
| B) localStorage 캐시 | 5분 TTL. 모달 재열 시 빠름 but stale 가능, 캐시 무효화 로직 추가 필요 |

---

**추천 조합: F-1 A + F-2 A** (가장 단순, SSOT, stale 위험 0)

두 결정을 알려주시면 Phase 4 구현을 시작합니다.
