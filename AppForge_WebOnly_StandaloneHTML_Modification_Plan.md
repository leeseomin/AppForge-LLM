# AppForge를 항상 웹앱으로 만들고 단일 실행 HTML까지 제공하기 위한 수정 설계

- 검토 대상: `AppForge-LLM-main`
- 목표: 사용자가 모바일 앱, 데스크톱 앱, CLI, API 서버, 자동화 도구 등 어떤 형태로 요청하더라도 **최종 구현 대상은 브라우저 웹앱으로 통일**한다.
- 필수 산출물: 최종 ZIP의 최상위에 **더블클릭만으로 실행되는 단일 HTML 파일**을 둔다.
- 권장 실행 파일명: `00_START_HERE.html`
- 문서 성격: 구현 전 수정 명세. 이 문서는 실제 소스 수정본이 아니라, 수정해야 할 위치와 완료 기준을 정리한 설계안이다.

---

## 1. 결론

현재 AppForge는 프롬프트에 포함된 단어에 따라 `mobile-app`, `desktop-app`, `cli-tool`, `api-service`, `automation`, `library-sdk` 등으로 실제 파이프라인을 나눈다. 또한 최종 패키징은 `dist/`, `build/`를 제외한 **소스 ZIP**을 만들 뿐이며, ZIP 내부에 더블클릭 실행용 HTML이 있는지는 검사하지 않는다.

따라서 다음과 같이 바꾸는 것이 타당하다.

1. **요청 의도와 배포 대상을 분리한다.**
   - “모바일 앱”은 터치 친화적 반응형 웹앱으로 해석한다.
   - “데스크톱 앱”은 데스크톱형 레이아웃과 단축키를 갖춘 웹앱으로 해석한다.
   - “CLI 도구”는 터미널형 웹 UI로 해석한다.
   - “API 서비스”는 브라우저 UI와 로컬 데모 엔진을 갖춘 웹앱으로 해석한다.
   - 그러나 어떤 경우에도 `delivery.target`은 `web`으로 고정한다.

2. **새 프로젝트의 유효 파이프라인을 `web-app-lite` 또는 `web-app`으로 제한한다.**
   - 수정 작업은 `feature`·`bugfix` 파이프라인을 계속 쓸 수 있다.
   - 단, 이 둘은 “배포 대상”이 아니라 “작업 방식”이며, 기존 웹 배포 계약을 절대 해제하지 못하게 한다.

3. **프롬프트 지시만으로 보장하지 않는다.**
   - 프로젝트 메타데이터, 파이프라인 게이트, 전용 도구, 최종 ZIP 검증까지 동일한 규칙을 강제해야 한다.

4. **전체 웹앱과 단일 HTML 동반판을 함께 제공한다.**
   - 전체 웹앱: 유지보수 가능한 소스 프로젝트.
   - `00_START_HERE.html`: 설치·터미널·로컬 서버 없이 실행되는 단일 파일.
   - 서버 의존 기능이 있는 경우 단일 HTML은 거짓으로 완전 동작을 주장하지 않고 `local-demo` 모드로 제공한다.

5. **단일 HTML 생성·검증이 실패하면 작업을 완료 처리하지 않는다.**
   - 현재처럼 “빌드 성공 + 소스 ZIP 성공”만으로 완료해서는 안 된다.

---

## 2. 현재 코드에서 확인된 핵심 문제

| 영역 | 현재 동작 | 문제 |
|---|---|---|
| `appforge/pipelines.py` | 모바일·데스크톱·CLI·API·자동화 등의 키워드를 실제 비웹 파이프라인으로 강하게 라우팅 | 입력 명령에 따라 결과물이 웹앱이 아닐 수 있음 |
| LLM 라우터 | 모든 파이프라인을 선택지로 주고 복잡한 웹 요청을 `fullstack-saas`로 승격 | 웹 전용 제품 정책을 우회할 수 있음 |
| `appforge/web_jobs.py` | API에서 전달된 `pipeline_name`을 그대로 사용할 수 있음 | 사용자가 비웹 파이프라인을 직접 지정 가능 |
| `appforge/web.py` | 생성·수정 요청 모델에 `pipeline` 필드 공개 | 웹 UI 외 클라이언트가 비웹 파이프라인을 강제 가능 |
| `appforge/cli.py` | `--pipeline`으로 임의 파이프라인 선택 가능 | CLI 경로에서도 웹 전용 정책 우회 가능 |
| `appforge/projects.py` | `project.json`에 배포 형식 계약이 없음 | 후속 단계와 재시도 과정에서 웹/HTML 요구가 사라질 수 있음 |
| `appforge/prompting.py` | 원문 프롬프트와 감지 스택을 기준으로 Flutter·Electron 지식도 주입 | “Flutter 앱”을 웹으로 바꾸더라도 에이전트가 Flutter를 생성할 수 있음 |
| 파이프라인 YAML | `run_build`, `release_readiness`, `archive_workspace`만 확인 | 단일 HTML 생성 여부와 `file://` 실행 가능 여부를 검증하지 않음 |
| `ArchiveWorkspaceTool` | 소스 파일만 ZIP으로 묶고 `dist/`, `build/`, `.appforge/` 제외 | 빌드 결과나 별도 생성된 실행 HTML이 최종 ZIP에서 사라질 수 있음 |
| `_validate_archive()` | ZIP 형식과 손상 파일만 확인 | 루트 HTML 누락, 외부 의존성, 해시 불일치를 탐지하지 못함 |
| 프리뷰 | `dist/`, `build/` 등의 `index.html`만 탐색 | 최종 사용자용 단일 HTML과 연결되어 있지 않음 |
| 완료 화면 | “소스 ZIP” 중심 | 초보 사용자가 무엇을 눌러 실행해야 하는지 불명확 |

가장 중요한 판단은 다음과 같다.

> `implementation.md`에 “HTML도 만들어라”라는 문장 하나를 추가하는 방식으로는 보장할 수 없다. 라우터, 프로젝트 계약, 생성 도구, 검증 게이트, ZIP 구조, 완료 UI를 함께 수정해야 한다.

---

## 3. 변경 후 제품 불변조건

새 프로젝트와 모든 후속 수정 작업에는 아래 조건을 불변조건으로 둔다.

### 3.1 배포 대상

```text
project.delivery.target == "web"
project.delivery.browser_first == true
project.delivery.standalone_html.required == true
```

사용자가 “iPhone 앱”, “Electron 앱”, “Python CLI”, “REST API”라고 입력해도 위 값은 바뀌지 않는다.

### 3.2 단일 HTML의 의미

`00_START_HERE.html`은 다음을 만족해야 한다.

- 압축 해제 후 파일을 더블클릭하면 기본 브라우저에서 열린다.
- `npm install`, `npm run dev`, Python 서버, Docker, 터미널이 필요하지 않다.
- 핵심 사용 흐름은 인터넷 연결 없이 시작할 수 있다.
- 필수 CSS, JavaScript, 이미지, 폰트, 초기 데이터가 파일 내부에 포함된다.
- 핵심 동작이 `localhost`, 상대 파일 `fetch()`, 외부 CDN에 의존하지 않는다.
- 로딩 직후 치명적인 JavaScript 오류가 없어야 한다.
- 실제 API 키, 토큰, 비밀번호를 포함하지 않는다.
- 서버가 필요한 기능은 명시적으로 `local-demo` 또는 `simulation`으로 표시한다.

### 3.3 완료 판정

다음 조건이 모두 참일 때만 작업을 `completed`로 바꾼다.

```text
full_web_build_passed
AND standalone_html_created
AND standalone_html_validated
AND file_protocol_smoke_passed
AND delivery_archive_created
AND delivery_archive_validated
```

단 하나라도 실패하면 다운로드를 활성화하지 않고 해당 단계에서 복구를 시도한다.

---

## 4. 권장 최종 ZIP 구조

현재처럼 소스 전체를 ZIP 루트에 흩어 놓기보다 초보자용 파일과 개발자용 소스를 분리하는 편이 낫다.

```text
my-app-webapp.zip
├── 00_START_HERE.html       # 더블클릭 실행용, 반드시 ZIP 루트
├── README_FIRST.md          # 비개발자용 1분 안내
├── DELIVERY.json            # 모드·검증·해시·제약 기록
├── source/                  # 유지보수 가능한 전체 웹앱 소스
│   ├── package.json
│   ├── src/
│   ├── README.md
│   └── ...
└── web-build/               # 선택 사항: 일반 정적 호스팅용 빌드
    └── ...
```

### 필수 규칙

- `00_START_HERE.html`은 하위 폴더가 아니라 **ZIP 루트**에 있어야 한다.
- `source/`는 개발자용이고, 일반 사용자는 열 필요가 없다.
- 아카이브 이름은 `*-source.zip` 대신 `*-webapp.zip` 또는 `*-delivery.zip`으로 바꾼다.
- ZIP 엔트리 작성 시 `00_START_HERE.html`을 먼저 기록하되, 파일 탐색기 정렬과 무관하게 이름 자체도 상단에 보이도록 `00_` 접두사를 사용한다.

### `README_FIRST.md`의 최소 내용

```md
# 바로 실행하기

1. `00_START_HERE.html`을 더블클릭합니다.
2. 기본 브라우저에서 앱이 열립니다.
3. 별도 설치나 터미널 명령은 필요하지 않습니다.

## 전체 개발 소스
`source/` 폴더에 있습니다.

## 제한 사항
서버·실결제·실제 이메일 발송 등이 필요한 기능은 이 단일 파일에서 로컬 데모로 동작합니다.
```

---

## 5. 입력 형태별 웹 변환 정책

라우터는 더 이상 “무슨 플랫폼으로 빌드할지”를 고르는 용도로 사용하지 않는다. 대신 원래 요청의 사용 감각을 웹에 옮기기 위한 `adaptation_profile`을 고른다.

| 사용자 요청 | 최종 해석 |
|---|---|
| Android·iOS·Flutter·React Native | 모바일 우선 반응형 웹앱 또는 PWA. 터치, 작은 화면, 하단 내비게이션, 안전 영역 고려 |
| Electron·Tauri·데스크톱 앱 | 넓은 화면, 패널 분할, 메뉴, 드래그 앤 드롭, 키보드 단축키를 갖춘 데스크톱형 웹앱 |
| CLI·터미널 도구 | 명령 입력창, 옵션 폼, 로그 콘솔, 결과 다운로드를 갖춘 브라우저 도구 |
| REST·GraphQL·API 서버 | 브라우저 UI + 요청 빌더 + 로컬 데모 어댑터. 필요하면 전체 소스에 선택적 서버 포함 |
| 자동화·봇 | 워크플로 빌더, 실행 시뮬레이터, 기록 화면을 갖춘 웹앱 |
| SDK·라이브러리 | 브라우저 플레이그라운드와 문서형 UI를 기본 제품으로 제공 |
| 데이터·ETL | CSV/JSON 업로드, 브라우저 내 처리, 대시보드, 내보내기 UI |
| 게임 | 브라우저 게임. 단일 HTML에 실행 코드와 필수 자산을 포함 |
| SaaS·인증·결제 | 전체 소스는 배포 가능한 웹앱으로 설계하되, 단일 HTML은 로컬 데모와 명확한 제한 표시 |

라우팅 결과 예시는 다음처럼 분리한다.

```json
{
  "requested_shape": "mobile",
  "adaptation_profile": "responsive-touch-web",
  "effective_pipeline": "web-app",
  "delivery_target": "web",
  "standalone_required": true
}
```

---

## 6. 파일별 수정 계획

## 6.1 `appforge/constants.py`

웹 배포 관련 상수를 추가한다.

```python
WEB_DELIVERY_TARGET = "web"
STANDALONE_ENTRY_NAME = "00_START_HERE.html"
DELIVERY_MANIFEST_NAME = "DELIVERY.json"
DELIVERY_README_NAME = "README_FIRST.md"
SOURCE_ARCHIVE_DIR = "source"
STANDALONE_WARN_BYTES = 25 * 1024 * 1024
STANDALONE_HARD_LIMIT_BYTES = 100 * 1024 * 1024
```

주의할 점은 `IGNORED_DIRS`에서 무작정 `dist`와 `build`를 제거하지 않는 것이다. 그렇게 하면 캐시와 불필요한 빌드 결과가 소스 ZIP에 대량 포함될 수 있다. 대신 새 패키징 도구가 검증된 파일만 명시적으로 ZIP에 넣도록 한다.

---

## 6.2 새 파일 `appforge/delivery.py`

배포 계약을 한 곳에서 관리한다.

권장 구성:

```python
@dataclass(frozen=True)
class WebDeliveryContract:
    target: str = "web"
    browser_first: bool = True
    standalone_required: bool = True
    standalone_entry: str = "00_START_HERE.html"
    offline_core_required: bool = True
    allow_external_runtime_assets: bool = False
    require_file_protocol_smoke: bool = True
    source_dir_in_archive: str = "source"
```

함수 예시:

```python
def default_web_delivery_contract() -> dict[str, Any]: ...
def normalize_public_pipeline(requested: str | None, complexity: str, existing_repo: bool) -> str: ...
def classify_adaptation_profile(prompt: str) -> dict[str, str]: ...
def assert_web_delivery_contract(project: dict[str, Any]) -> None: ...
```

이 모듈을 라우터, 프로젝트 초기화, 프롬프트, 웹 잡, CLI가 공통으로 사용하게 해야 규칙이 흩어지지 않는다.

---

## 6.3 `appforge/pipelines.py`

### 현재 바꿔야 할 부분

- `_keyword_scores()`의 비웹 `hard_rules`
- `_route_prompt_for_llm()`의 전체 파이프라인 선택지
- `_coerce_llm_route()`의 `fullstack-saas` 승격
- `auto_select_pipeline()`·`select_pipeline()`의 최종 반환값
- 공개 파이프라인 목록

### 권장 정책

#### 새 프로젝트

```text
trivial/small  -> web-app-lite
standard/complex -> web-app
```

#### 기존 프로젝트 수정

```text
명백한 오류 수정 -> bugfix
그 외 변경       -> feature
```

`feature`와 `bugfix`는 기존 프로젝트의 `delivery.target=web`을 상속해야 한다.

### 비웹 파이프라인 처리

기존 YAML 파일을 즉시 삭제할 필요는 없다. 오래된 체크포인트와 테스트 호환을 위해 내부에는 남기되 다음처럼 분리한다.

```python
PUBLIC_GREENFIELD_PIPELINES = {"web-app-lite", "web-app"}
PUBLIC_REVISION_PIPELINES = {"feature", "bugfix"}
LEGACY_PIPELINES = {
    "mobile-app", "desktop-app", "cli-tool", "api-service",
    "automation", "library-sdk", "data-app", "prototype",
    "fullstack-saas",
}
```

- `list_pipeline_names()`은 내부용으로 유지한다.
- `list_public_pipeline_names()`를 새로 만든다.
- 사용자 입력으로 레거시 파이프라인이 들어오면 실패시키기보다 `web-app` 또는 `web-app-lite`로 정규화하고 이벤트에 기록한다.

예:

```json
{
  "requested_pipeline": "mobile-app",
  "effective_pipeline": "web-app",
  "normalized": true,
  "reason": "Public AppForge output is always a browser web application."
}
```

### LLM 라우터 스키마 변경

LLM이 파이프라인을 자유롭게 고르게 하지 말고 아래만 반환하게 한다.

```json
{
  "complexity": "small | standard | complex",
  "requested_shape": "mobile | desktop | cli | api | automation | library | data | game | general",
  "adaptation_profile": "string",
  "confidence": 0.0,
  "rationale": "string"
}
```

실제 파이프라인은 애플리케이션 코드가 결정한다. 이렇게 해야 LLM 오분류가 배포 정책을 깨지 못한다.

---

## 6.4 `appforge/projects.py`

`initialize_project()`가 모든 프로젝트에 배포 계약을 기록하도록 한다.

권장 `project.json` 추가 필드:

```json
{
  "delivery": {
    "target": "web",
    "browser_first": true,
    "standalone_html": {
      "required": true,
      "entry": "00_START_HERE.html",
      "offline_core_required": true,
      "file_protocol_required": true,
      "external_runtime_assets_allowed": false
    },
    "archive": {
      "root_entry_required": true,
      "source_dir": "source",
      "readme": "README_FIRST.md",
      "manifest": "DELIVERY.json"
    }
  },
  "adaptation": {
    "requested_shape": "mobile",
    "profile": "responsive-touch-web",
    "original_pipeline_request": "mobile-app"
  }
}
```

### 기존 프로젝트 마이그레이션

`load_project()`에서 `delivery`가 없는 과거 프로젝트를 처리한다.

1. 브라우저 엔트리나 `package.json`·Vite 구성이 확인되면 기본 웹 계약을 자동 삽입한다.
2. 명백한 비웹 레거시 프로젝트라면 조용히 웹으로 간주하지 않는다.
3. 사용자용 웹 제품에서는 “새 웹 변환 작업”으로 복제하거나, 최소한 명확한 변환 오류를 반환한다.

수정 작업에서는 이전 `00_START_HERE.html`과 `DELIVERY.json`을 그대로 재사용하지 말고 항상 삭제 후 재생성한다. 그렇지 않으면 소스는 수정됐지만 실행 HTML은 이전 버전인 심각한 불일치가 생긴다.

---

## 6.5 `appforge/prompting.py`

프롬프트는 웹 전용 정책을 보조해야 하지만, 유일한 강제 수단이어서는 안 된다.

### 새 메타 스킬

`appforge/resources/skills/meta/web-delivery-contract.md`를 추가한다.

핵심 내용:

- 모든 결과물은 브라우저 웹앱이다.
- 원문의 모바일·데스크톱·CLI·API 표현은 UX 형태로만 번역한다.
- 네이티브 바이너리, Electron/Tauri 패키지, Flutter 앱, 터미널 전용 결과를 최종 제품으로 만들지 않는다.
- 유지보수 가능한 전체 소스와 단일 HTML을 모두 만든다.
- 서버 의존 기능은 공유 인터페이스를 두고 단일 HTML용 로컬 어댑터를 제공한다.
- 외부 CDN, 원격 폰트, 하드코딩된 비밀정보를 단일 HTML에 넣지 않는다.

### 모든 프롬프트에 주입

다음 두 함수에 동일한 계약을 넣어야 한다.

- `build_stage_prompt()`
- `build_repair_stage_prompt()`

일반 단계에만 넣고 repair 프롬프트에서 누락하면 재시도 시 정책이 사라질 수 있다.

권장 섹션:

```md
## Non-negotiable web delivery contract
- Effective delivery target: browser web application.
- Required archive-root entry: `00_START_HERE.html`.
- It must run by double-click under `file://` without a local server.
- Do not satisfy the original request with native, desktop-only, terminal-only, or server-only output.
- Preserve the requested interaction model by adapting it to the browser.
```

### `_relevant_knowledge()` 수정

현재 원문에 Flutter·Electron 단어가 있거나 감지 스택에 해당 프레임워크가 있으면 관련 지식 파일이 선택될 수 있다. 웹 전용 새 프로젝트에서는 다음을 막는다.

```python
if project["delivery"]["target"] == "web" and project_is_greenfield:
    suppress = {"stacks/flutter.md", "stacks/electron-tauri.md"}
```

대신 `stacks/browser-delivery.md`를 새로 추가하고 다음을 안내한다.

- 경량 앱: 바닐라 HTML/CSS/JS 또는 TypeScript
- 표준 앱: Vite 기반 SPA
- `base: "./"`
- 정적 빌드 가능
- standalone 전용 빌드 스크립트
- 서버 기능은 어댑터 분리

원문 요청은 보존하되, 모델이 실제 구현 기준으로 보는 `normalized_request.md`도 생성하는 편이 안전하다.

---

## 6.6 단계 스킬 파일 수정

다음 파일을 갱신한다.

- `resources/skills/stages/intake.md`
- `resources/skills/stages/engineering-spec.md`
- `resources/skills/stages/specification.md`
- `resources/skills/stages/architecture.md`
- `resources/skills/stages/experience.md`
- `resources/skills/stages/implementation.md`
- `resources/skills/stages/verification.md`
- `resources/skills/stages/release.md`
- `resources/skills/stages/handoff.md`
- `resources/skills/meta/definition-of-done.md`
- `resources/skills/meta/pipeline-router.md`

### 주요 추가 내용

#### Intake·Specification

- 원래 요청의 플랫폼 명칭과 실제 웹 구현을 분리한다.
- “모바일”은 화면 크기·터치 요구로, “CLI”는 상호작용 스타일로 재정의한다.
- `standalone core journey`를 별도 요구사항으로 작성한다.

#### Architecture

- 전체 웹앱의 서비스 어댑터와 로컬 단일 HTML 어댑터를 분리한다.
- 핵심 도메인 로직은 브라우저에서 재사용 가능하게 유지한다.
- Next.js SSR, 서버 전용 렌더링, 네이티브 전용 프레임워크를 기본 선택으로 삼지 않는다.

#### Implementation

- 일반 빌드와 standalone 빌드를 모두 만든다.
- `package.json`에 가능하면 다음 스크립트를 요구한다.

```json
{
  "scripts": {
    "build": "vite build",
    "build:standalone": "vite build --mode standalone"
  }
}
```

- 단일 HTML이 원격 URL을 요구하지 않게 한다.
- 로컬 데이터는 초기 HTML에 포함하거나 브라우저 저장소를 사용한다.
- 저장소 사용이 차단될 경우 인메모리·내보내기 폴백을 제공한다.

#### Verification

- 일반 HTTP 프리뷰만 확인하지 않는다.
- `file://.../00_START_HERE.html`을 실제 브라우저로 연다.
- 네트워크 요청, 콘솔 오류, 빈 화면, 누락 자산을 검사한다.

#### Release·Handoff

- “소스 ZIP 생성”이 아니라 “웹 배포 패키지 생성”을 완료 기준으로 바꾼다.
- `README_FIRST.md`, `DELIVERY.json`, 루트 HTML의 존재를 명시한다.

---

## 6.7 파이프라인 YAML 수정

### 새 단계 `web_delivery`

`web-app.yaml`, `web-app-lite.yaml`, `feature.yaml`, `bugfix.yaml`에 공통으로 넣는다.

권장 위치:

- `web-app`: `security` 다음, `release` 전
- `web-app-lite`: `verification` 다음, `handoff` 전
- `feature`: `security` 다음, `release` 전
- `bugfix`: `security` 다음, `release` 전

예시:

```yaml
- name: web_delivery
  description: Build and validate the browser-first delivery package and standalone HTML entry.
  skill: stages/web-delivery.md
  produces:
  - web_delivery_manifest
  tools:
  - run_build
  - build_standalone_html
  - validate_web_delivery
  checkpoint: true
  approval: false
  review_focus:
  - The full product is a browser application
  - The standalone entry runs from file protocol
  - No required runtime asset depends on an external URL
  - Server-dependent features are labeled as local demo when applicable
  success_criteria:
  - 00_START_HERE.html exists and is self-contained
  - File-protocol smoke test passes
  - Web delivery manifest records hashes and limitations
  gates:
  - tool: build_standalone_html
    required: true
  - tool: validate_web_delivery
    required: true
```

새 스킬 `resources/skills/stages/web-delivery.md`도 추가한다.

### `handoff` 게이트 수정

기존 `archive_workspace`는 소스 ZIP 용도이므로 사용자용 웹 파이프라인에서는 다음 중 하나로 바꾼다.

```yaml
- tool: package_web_delivery
  required: true
```

또는 패키징을 `web_jobs.py`의 시스템 단계에서만 수행하고, handoff는 `validate_web_delivery`만 재검증한다. 권장 방식은 **시스템 단계가 최종 ZIP을 확정**하는 것이다. 에이전트가 산출물 설명은 작성하되, 실제 패키징·검증은 결정론적 코드가 담당하는 편이 안전하다.

### 레거시 파이프라인

사용자 공개 경로에서 선택할 수 없게 한다. 다만 우회 가능성을 완전히 막기 위해 `web_jobs.py`의 전역 패키징 단계는 어떤 파이프라인이 실행됐든 웹 계약을 검사해야 한다.

---

## 6.8 새 도구 `appforge/tooling/tools/web_delivery.py`

도구 레지스트리는 모듈을 자동 탐색하므로 새 파일에 `Tool` 하위 클래스를 정의하면 등록할 수 있다.

권장 도구는 세 개다.

### A. `BuildStandaloneHtmlTool`

이름:

```python
name = "build_standalone_html"
capability = "release"
```

책임:

1. 전체 웹앱 빌드를 실행하거나 기존 성공 빌드를 확인한다.
2. standalone 전용 빌드 스크립트가 있으면 우선 사용한다.
3. 빌드된 HTML의 로컬 CSS·JS·이미지·폰트·아이콘을 인라인한다.
4. CSS의 `url(...)` 참조도 데이터 URI로 변환한다.
5. `<base href>`와 절대 `/assets/...` 경로를 제거하거나 상대화한다.
6. 동적 청크·Web Worker·WASM이 있으면 Blob 또는 내장 바이트로 처리한다.
7. 결과를 `.appforge/deliverables/00_START_HERE.html`에 기록한다.
8. SHA-256, 크기, 생성 시각을 반환한다.

HTML을 정규식으로 합치지 말고 HTML 파서와 안전한 경로 해석을 사용한다.

### B. `ValidateWebDeliveryTool`

이름:

```python
name = "validate_web_delivery"
```

필수 검사:

- 문서 구조: doctype, `html`, `head`, `body`
- 남아 있는 로컬 자산 참조 없음
- 필수 `http://`, `https://`, `//cdn...` 런타임 의존성 없음
- `localhost`, `127.0.0.1`, WebSocket 강제 의존성 없음
- 절대 파일 경로 없음
- 미해결 모듈 import·동적 청크 없음
- 비밀정보 검사 통과
- HTML 크기 기록
- `file://` 브라우저 로딩 통과
- 초기 화면이 비어 있지 않음
- uncaught exception·unhandled rejection 없음
- 외부 네트워크 요청 0건

허용 스킴은 기본적으로 다음뿐이다.

```text
file:
data:
blob:
about:
```

외부 문서 링크는 클릭 전까지는 허용할 수 있지만, 앱 초기화나 핵심 흐름이 외부 URL을 요구하면 실패시킨다.

### C. `PackageWebDeliveryTool`

이름:

```python
name = "package_web_delivery"
```

책임:

- 사용자용 파일은 ZIP 루트에 둔다.
- 프로젝트 소스는 `source/` 아래에 매핑한다.
- `.git`, `.appforge`, 의존성, 캐시, 비밀 파일은 제외한다.
- 검증된 단일 HTML만 포함한다.
- `DELIVERY.json`과 `README_FIRST.md`를 생성한다.
- ZIP 내부 경로 순회, 심볼릭 링크, 절대 경로를 차단한다.
- 아카이브 생성 후 다시 열어 내용과 해시를 재검증한다.

기존 `ArchiveWorkspaceTool`에 모든 기능을 억지로 넣기보다 별도 도구를 두는 편이 역할이 명확하다. 기존 도구는 개발자용 소스 아카이브 기능으로 유지할 수 있다.

---

## 6.9 단일 HTML 생성 방식

모든 앱을 같은 방식으로 합칠 수 없으므로 세 가지 모드를 둔다.

### 모드 1: `native-single-file`

대상:

- 작은 계산기, 메모, 타이머, 변환기
- 간단한 Canvas 게임
- 외부 서버가 필요 없는 도구

방식:

- 처음부터 `00_START_HERE.html` 하나에 CSS·JS를 포함한다.
- 전체 소스에서는 파일을 분리해 유지해도 최종 단계에서 합친다.

### 모드 2: `bundled-single-file`

대상:

- Vite + Vue/React/Svelte 등 SPA
- 여러 모듈과 자산이 있는 앱

방식:

- 일반 빌드는 유지한다.
- standalone 모드에서 CSS 분할과 동적 청크를 끄고 모든 자산을 인라인한다.
- Vite의 경우 `base: "./"`, 단일 청크, 높은 `assetsInlineLimit` 또는 신뢰 가능한 single-file 플러그인을 사용한다.
- 최종 도구가 다시 외부 참조를 검사한다.

### 모드 3: `local-demo-companion`

대상:

- 서버 인증, 실제 결제, 이메일 발송, 멀티테넌트 DB, 비밀 API 키가 필요한 앱

방식:

- 전체 `source/`에는 실제 배포 구조를 둔다.
- 단일 HTML에는 동일한 UI와 핵심 흐름을 보여 주는 로컬 어댑터를 둔다.
- 데이터는 메모리, IndexedDB 또는 내장 샘플로 처리한다.
- 실제 결제·메일·외부 API 호출을 성공한 것처럼 위장하지 않는다.
- 화면과 `DELIVERY.json`에 제한 사항을 표시한다.

이 구분이 있어야 “항상 단일 HTML 제공”과 “정직한 제품 동작”을 동시에 지킬 수 있다.

---

## 6.10 `appforge/tooling/tools/release.py`

### `ReleaseReadinessTool`

`project.delivery.target == "web"`이면 다음을 필수 체크로 추가한다.

```text
standalone_artifact
web_delivery_manifest
standalone_validation_passed
file_protocol_smoke_passed
root_entry_contract
```

현재의 `source_present`, `README.md`, verification artifact 검사만으로는 부족하다.

### `ArtifactInventoryTool`

다음도 인벤토리에 포함한다.

- `.appforge/deliverables/00_START_HERE.html`
- `.appforge/artifacts/web_delivery_manifest.json`
- 사용자용 `*-webapp.zip`

### `ArchiveWorkspaceTool`

기존 동작은 보존하되 사용자용 웹 완료 경로에서는 사용하지 않거나, `delivery.target=web`일 때 새 패키징 도구로 위임한다.

`IGNORED_DIRS`에서 `dist`·`build`를 전역 해제하는 방식은 피한다.

---

## 6.11 새 스키마 `web_delivery_manifest.schema.json`

경로:

```text
appforge/resources/schemas/artifacts/web_delivery_manifest.schema.json
```

권장 형태:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Web Delivery Manifest",
  "type": "object",
  "required": [
    "schema_version",
    "target",
    "full_app",
    "standalone",
    "checks",
    "limitations"
  ],
  "properties": {
    "schema_version": {"const": "1.0"},
    "target": {"const": "web"},
    "full_app": {
      "type": "object",
      "required": ["source_root", "build_command"],
      "properties": {
        "source_root": {"type": "string"},
        "build_command": {"type": "string"},
        "build_output": {"type": ["string", "null"]}
      }
    },
    "standalone": {
      "type": "object",
      "required": [
        "path", "mode", "sha256", "bytes",
        "file_protocol_supported", "external_runtime_requests"
      ],
      "properties": {
        "path": {"const": "00_START_HERE.html"},
        "mode": {"enum": ["full", "local-demo"]},
        "sha256": {"type": "string"},
        "bytes": {"type": "integer", "minimum": 1},
        "file_protocol_supported": {"const": true},
        "external_runtime_requests": {"const": 0},
        "core_journeys": {"type": "array", "items": {"type": "string"}}
      }
    },
    "checks": {
      "type": "object",
      "required": [
        "html_structure", "asset_inlining", "secret_scan",
        "file_protocol_smoke", "console_errors", "network_isolation"
      ]
    },
    "limitations": {
      "type": "array",
      "items": {"type": "string"}
    }
  }
}
```

`pyproject.toml`은 이미 `resources/schemas/artifacts/*.json`을 패키지 데이터에 포함하므로 새 스키마 파일은 현재 glob으로 배포된다.

### 기존 스키마 보강

- `implementation_report`: 일반 빌드와 standalone 구현 경로
- `verification_report`: `file://` 검증 결과와 네트워크 요청 수
- `release_report`: delivery manifest와 ZIP 해시
- `handoff_report`: 비개발자 실행 절차와 `standalone.mode`

---

## 6.12 `appforge/web_jobs.py`

이 파일이 실제 사용자용 완료 흐름을 통제하므로 가장 중요한 수정 지점이다.

### A. `_route_initial_prompt()`

- 전달받은 `pipeline_name`을 그대로 사용하지 않는다.
- 공개 파이프라인 정규화 함수를 통과시킨다.
- `requested_shape`와 `effective_pipeline`을 잡 데이터에 함께 저장한다.

### B. `_choose_revision_pipeline()`

`feature`·`bugfix` 선택은 유지해도 된다. 단, 원본 프로젝트의 웹 배포 계약을 복사하고 강제한다.

### C. 잡 초기 상태

현재 `preview`, `download` 외에 다음 상태를 추가한다.

```json
{
  "delivery": {
    "target": "web",
    "standalone_required": true,
    "entry": "00_START_HERE.html"
  },
  "standalone": {
    "available": false,
    "url": null,
    "filename": "00_START_HERE.html",
    "size_bytes": null,
    "mode": null,
    "limitations": []
  }
}
```

### D. 시스템 단계명 변경

기존:

```text
download_package
```

권장:

```text
web_delivery_package
```

표시 문구:

```text
웹앱과 바로 실행 HTML을 검증하고 있습니다.
```

### E. 파이프라인 완료 후 흐름

현재 `_ensure_archive()`만 호출하는 부분을 다음으로 교체한다.

```python
standalone = self._build_standalone(layout)
self._validate_standalone(layout, standalone)
archive = self._package_web_delivery(layout, standalone)
self._validate_delivery_archive(archive)
```

각 단계는 전용 `Tool`을 호출하고, 실패 시 고유 오류 코드를 반환한다.

권장 오류 코드:

- `WEB_DELIVERY_BUILD_FAILED`
- `STANDALONE_HTML_MISSING`
- `STANDALONE_HTML_INVALID`
- `STANDALONE_EXTERNAL_DEPENDENCY`
- `FILE_PROTOCOL_SMOKE_FAILED`
- `DELIVERY_ARCHIVE_INVALID`
- `DELIVERY_MANIFEST_MISMATCH`

### F. 완료 데이터

```json
{
  "status": "completed",
  "message": "웹앱과 바로 실행 HTML이 준비되었습니다.",
  "standalone": {
    "available": true,
    "url": "/api/jobs/{job_id}/standalone",
    "filename": "00_START_HERE.html",
    "size_bytes": 123456,
    "mode": "full"
  },
  "download": {
    "available": true,
    "url": "/api/jobs/{job_id}/download",
    "filename": "my-app-webapp.zip"
  }
}
```

### G. `_validate_archive()` 강화

현재는 ZIP 손상 여부만 검사한다. 다음을 추가한다.

1. 절대 경로, `..`, 역슬래시 기반 우회, 심볼릭 링크 차단
2. 루트 `00_START_HERE.html` 정확히 1개 요구
3. 루트 `README_FIRST.md` 요구
4. 루트 `DELIVERY.json` 요구
5. `source/` 아래 소스 파일 존재 요구
6. manifest의 SHA-256과 ZIP 내부 HTML 실제 해시 비교
7. HTML을 ZIP 내부에서 다시 읽어 standalone 정적 검사 재수행
8. 빈 HTML·비정상적으로 작은 HTML 거부
9. 중복 파일명·대소문자 충돌 검사

### H. 수정 작업 복사

`_copy_workspace_for_revision()`에서 다음 이전 산출물을 제외한다.

```text
00_START_HERE.html
DELIVERY.json
README_FIRST.md
.appforge/deliverables/
기존 *-webapp.zip
```

수정 후 반드시 새로 생성해야 한다.

### I. 프리뷰

`build_preview()`는 먼저 검증된 standalone을 사용한다.

- 생성 전: 기존 `dist/` 탐색 가능
- 생성 후: `00_START_HERE.html`을 우선 프리뷰
- 단, HTTP 프리뷰 성공을 `file://` 검증의 대체로 인정하지 않는다.

---

## 6.13 `appforge/web.py`

### 공개 요청 모델

`CreateJobRequest.pipeline`과 `ReviseJobRequest.pipeline`을 제거하는 것이 가장 단순하다.

호환성을 유지해야 한다면 필드는 deprecated로 남기되 서버에서 다음처럼 정규화한다.

- `web-app`, `web-app-lite`, `feature`, `bugfix`: 조건에 맞게 사용
- 그 외: 웹 파이프라인으로 변환
- 응답의 `routing`에 변환 사실 기록

비웹 값을 그대로 `load_pipeline()`에 넘겨서는 안 된다.

### 새 엔드포인트

```text
GET /api/jobs/{job_id}/standalone
```

- 검증된 `00_START_HERE.html`만 내려준다.
- `Content-Disposition: attachment; filename="00_START_HERE.html"`
- 검증 전이나 실패 상태에서는 제공하지 않는다.

기존 ZIP 다운로드도 유지한다.

### Health 응답

```json
{
  "capabilities": {
    "delivery_target": "web",
    "standalone_html": true,
    "standalone_entry": "00_START_HERE.html",
    "file_protocol_validation": true
  }
}
```

---

## 6.14 `appforge/cli.py`

사용자가 CLI로 실행해도 정책이 달라지면 안 된다.

- `new`, `forge`의 `--pipeline` 선택지를 공개 웹 파이프라인으로 제한한다.
- 레거시 값을 받으면 웹으로 정규화하고 메시지를 표시한다.
- `route`는 비웹 파이프라인명이 아니라 adaptation profile과 실제 웹 파이프라인을 출력한다.
- `pipelines`는 기본적으로 공개 웹 파이프라인만 보여 준다.
- 과거 프로젝트 복구용 레거시 목록은 숨은 개발 옵션으로만 둔다.

출력 예:

```text
Requested shape: mobile
Adaptation: responsive touch-first web app
Effective pipeline: web-app
Delivery: source/ + 00_START_HERE.html
```

---

## 6.15 프런트엔드 수정

### `frontend/src/api.ts`

- `createJob()`·수정 API 옵션에서 공개 `pipeline`을 제거한다.
- standalone 다운로드 API를 추가한다.

### `frontend/src/types.ts`

```ts
export interface StandaloneState {
  available: boolean;
  url: string | null;
  filename: string;
  size_bytes: number | null;
  mode: 'full' | 'local-demo' | null;
  limitations: string[];
}
```

잡 타입에 `delivery`, `standalone`, `routing.adaptation_profile`을 추가한다.

### `frontend/src/components/ComposerCard.vue`

입력창 주변에 다음 정책을 명확히 표시한다.

```text
모든 요청은 브라우저 웹앱으로 구현됩니다.
완료 ZIP에는 더블클릭 실행용 00_START_HERE.html과 전체 소스가 함께 포함됩니다.
```

### `frontend/src/components/JobPanel.vue`

현재의 “자동 파이프라인 · mobile-app” 같은 문구 대신 다음을 보여 준다.

```text
웹앱 제작 · 모바일형 UX로 변환
```

완료 시 버튼 우선순위:

1. **바로 실행 HTML 받기**
2. **전체 웹앱 ZIP 받기**
3. 프리뷰 열기

`local-demo`인 경우 버튼 근처에 다음을 표시한다.

```text
이 HTML은 설치 없이 실행되는 로컬 데모입니다. 실제 서버 기능은 source/의 전체 웹앱에서 연결할 수 있습니다.
```

### `frontend/src/components/StageTimeline.vue`

`download_package` 대신 `web_delivery_package` 단계와 다음 상태를 표현한다.

- 단일 HTML 생성
- 파일 실행 검증
- 최종 패키지 구성

---

## 7. `file://` 실행에서 반드시 고려할 기술 제약

단순히 모든 파일을 HTML에 붙였다고 해서 더블클릭 실행이 보장되지는 않는다.

### 7.1 ES 모듈과 동적 import

- 로컬 경로의 ES 모듈은 브라우저 보안 정책에 걸릴 수 있다.
- 최종 파일에서는 모듈 의존성을 하나의 번들로 만든다.
- 동적 import 청크가 남아 있으면 실패시킨다.

### 7.2 `fetch()`로 로컬 JSON 읽기

`file://`에서 `fetch('./data.json')`은 흔히 차단된다. 초기 데이터는 HTML 안에 JSON 스크립트, 압축 문자열 또는 JS 객체로 포함한다.

### 7.3 Service Worker·PWA

Service Worker는 일반적으로 `file://`에서 동작하지 않는다. 전체 배포 웹앱에서는 PWA를 지원해도, standalone에서는 등록을 건너뛰어야 한다.

### 7.4 저장소

`localStorage`와 IndexedDB의 `file://` 동작은 브라우저별 차이가 있을 수 있다.

- capability detection
- 인메모리 폴백
- JSON 내보내기·가져오기

를 제공하는 편이 안전하다.

### 7.5 외부 API와 CORS

- 핵심 흐름은 외부 API 없이 시작해야 한다.
- 사용자 입력 API 키를 지원할 수는 있으나 파일에 키를 내장하지 않는다.
- CORS로 실패하는 경우 로컬 데모 데이터를 제공한다.

### 7.6 Worker·WASM·대형 자산

- Worker 스크립트는 Blob URL로 생성한다.
- WASM은 Base64 또는 바이트 배열로 내장한다.
- 대형 비디오·3D 자산은 단일 HTML 크기를 크게 늘린다.
- 25 MiB 이상은 경고, 100 MiB 이상은 최적화 또는 명시적 예외를 요구한다.

---

## 8. 브라우저 스모크 테스트 설계

구조 검사만으로는 빈 화면을 잡을 수 없다. 사용자용 완료 판정에서는 실제 브라우저 실행 검사가 필요하다.

권장 절차:

1. Chrome/Chromium/Edge 또는 Playwright 브라우저를 찾는다.
2. `file:///.../00_START_HERE.html`을 연다.
3. 모든 `http`, `https`, `ws`, `wss` 요청을 기록하고 차단한다.
4. `DOMContentLoaded` 이후 앱 루트가 보이는지 확인한다.
5. 일정 시간 동안 uncaught exception과 unhandled rejection을 수집한다.
6. 로딩 화면에 영구 정지하지 않는지 확인한다.
7. manifest에 정의된 최소 핵심 동작을 수행한다.
8. 스크린샷과 로그를 `.appforge/reports/`에 남긴다.

사용자용 배포 프로필에서는 브라우저 실행기를 찾지 못한 상태를 “검증 통과”로 간주하면 안 된다. 설치 환경 때문에 반드시 선택적이어야 한다면 상태를 `unverified`로 남기고 사용자용 완료를 차단하는 편이 정직하다.

---

## 9. `DELIVERY.json` 예시

```json
{
  "schema_version": "1.0",
  "app_name": "field-inspection-app",
  "delivery_target": "web",
  "requested_shape": "mobile",
  "adaptation_profile": "responsive-touch-web",
  "standalone": {
    "entry": "00_START_HERE.html",
    "mode": "full",
    "sha256": "...",
    "bytes": 2845112,
    "file_protocol_supported": true,
    "external_runtime_requests": 0,
    "core_journeys_verified": [
      "Create an inspection",
      "Add a photo placeholder",
      "Export JSON"
    ]
  },
  "full_app": {
    "source_dir": "source",
    "build_command": "npm run build",
    "standalone_build_command": "npm run build:standalone"
  },
  "checks": {
    "html_structure": "passed",
    "asset_inlining": "passed",
    "secret_scan": "passed",
    "file_protocol_smoke": "passed",
    "console_errors": 0,
    "network_isolation": "passed"
  },
  "limitations": []
}
```

서버 의존 앱이라면:

```json
{
  "standalone": {
    "mode": "local-demo"
  },
  "limitations": [
    "Actual account authentication requires the deployable server in source/.",
    "Payment actions are simulated and do not create real charges."
  ]
}
```

---

## 10. 테스트 수정·추가 계획

## 10.1 `tests/test_pipelines.py`

기존의 다음 기대값을 바꾼다.

- Flutter 요청 → `mobile-app`이 아니라 `web-app`
- CLI 요청 → `cli-tool`이 아니라 `web-app-lite` 또는 `web-app`
- API 요청 → `api-service`가 아니라 `web-app`
- Electron 요청 → `desktop-app`이 아니라 `web-app`

추가 검증:

- adaptation profile은 원래 의도를 유지한다.
- 복잡도에 따라 lite/standard만 갈린다.
- 기존 저장소는 feature/bugfix를 쓰되 delivery target은 web이다.
- LLM 라우터가 잘못된 비웹 이름을 반환해도 웹 파이프라인으로 정규화된다.

## 10.2 `tests/test_projects_and_checkpoints.py`

- 모든 새 프로젝트에 `delivery.target=web` 존재
- standalone 계약 필드 존재
- 과거 프로젝트의 웹 계약 마이그레이션
- 재시도와 resume 후에도 계약 보존

## 10.3 `tests/test_prompting.py`

- 일반 프롬프트와 repair 프롬프트 모두 웹 배포 계약 포함
- 모바일 요청에서도 Flutter 구현 지식이 주입되지 않음
- Electron 요청에서도 네이티브 패키징을 최종 목표로 안내하지 않음

## 10.4 `tests/test_tools.py`

`BuildStandaloneHtmlTool` 테스트:

- CSS·JS 인라인
- 이미지·폰트 데이터 URI 변환
- CSS `url()` 재작성
- 절대 `/assets` 제거
- 동적 청크 탐지
- 외부 CDN 탐지
- localhost 탐지
- 비밀정보 탐지
- 경로 순회 차단

`PackageWebDeliveryTool` 테스트:

- 루트 HTML 포함
- `README_FIRST.md`, `DELIVERY.json` 포함
- 소스가 `source/` 아래에 있음
- `node_modules`, `.git`, `.env`, 캐시 제외
- manifest 해시 일치
- 손상·중복·순회 엔트리 거부

## 10.5 `tests/test_web.py`

- 비웹 pipeline API 입력이 웹으로 정규화됨
- standalone 실패 시 `completed`가 되지 않음
- 완료된 잡에 standalone 다운로드 정보 존재
- ZIP 검증이 루트 HTML 누락을 거부
- 수정 작업이 이전 standalone을 재사용하지 않음
- 프리뷰가 검증된 standalone을 우선 사용

현재 여러 테스트가 `pipeline_name="prototype"`에 의존하므로 내부 테스트 픽스처는 `web-app-lite`로 바꾸거나, 파이프라인 자체를 시험하는 경우에만 레거시 로더를 직접 사용한다.

## 10.6 `tests/test_cli.py`

- `route`가 `mobile-app`을 직접 출력하지 않음
- 실제 결과가 웹 파이프라인과 adaptation profile을 함께 표시
- 비웹 `--pipeline`이 정규화되거나 명확히 거부됨

## 10.7 프런트엔드 테스트

- 완료 화면에 HTML 버튼이 ZIP 버튼보다 먼저 표시
- `local-demo` 제한 문구 표시
- “모든 요청은 웹앱” 안내 표시
- 비웹 파이프라인 선택 UI가 없음

---

## 11. 권장 구현 순서

### P0 — 정책을 우회할 수 없게 만들기

1. `delivery.py`와 프로젝트 배포 계약 추가
2. 새 프로젝트 라우팅을 `web-app-lite`·`web-app`으로 제한
3. API·CLI의 비웹 파이프라인 직접 지정 차단 또는 정규화
4. 모든 stage/repair 프롬프트에 웹 계약 주입
5. Flutter·Electron 지식 자동 주입 억제
6. 라우팅·프로젝트·프롬프트 테스트 수정

이 단계만으로도 “비웹 앱이 생성되는 문제”는 크게 줄지만, 단일 HTML 보장은 아직 불완전하다.

### P1 — 단일 HTML을 진짜 배포 계약으로 만들기

1. `web_delivery` 단계와 artifact schema 추가
2. `BuildStandaloneHtmlTool` 구현
3. `ValidateWebDeliveryTool` 구현
4. `PackageWebDeliveryTool` 구현
5. `ReleaseReadinessTool` 강화
6. `web_jobs.py` 완료 흐름 교체
7. ZIP 구조·해시·루트 엔트리 검증
8. 수정 작업에서 stale standalone 제거

이 단계가 완료되어야 사용자 요구를 실질적으로 만족한다.

### P2 — 초보자 UX 완성

1. standalone 직접 다운로드 엔드포인트
2. 완료 화면의 “바로 실행 HTML” 기본 버튼
3. `local-demo`·제약 표시
4. 검증 스크린샷·로그 표시
5. 브라우저별 smoke matrix
6. 대형 자산 최적화와 파일 크기 경고

---

## 12. 하지 말아야 할 수정

### 12.1 프롬프트 문장 하나만 추가

모델이 누락하거나 repair 단계에서 잊을 수 있다. 시스템 수준 보장이 아니다.

### 12.2 모든 요청을 무조건 `web-app-lite`로 보내기

복잡한 앱의 구조·검증 품질이 낮아질 수 있다. lite와 standard의 구분은 유지해야 한다.

### 12.3 `dist/`·`build/`를 전역 아카이브 허용

불필요한 캐시, 중복 파일, 대형 결과가 ZIP에 섞인다. 검증된 산출물만 새 패키징 도구가 명시적으로 넣어야 한다.

### 12.4 단순히 `index.html`을 ZIP 루트로 복사

외부 JS·CSS·이미지를 참조하는 `index.html`은 단독 실행 파일이 아니다. 참조 인라인과 `file://` 검증이 필요하다.

### 12.5 HTTP 프리뷰 성공을 더블클릭 실행 성공으로 간주

HTTP 서버에서는 되지만 `file://`에서는 실패하는 모듈, fetch, CORS 문제가 많다. 두 검증을 분리해야 한다.

### 12.6 서버 기능을 단일 HTML에서 실제로 성공한 것처럼 위장

결제, 인증, 이메일, 비밀 API 호출은 로컬 데모라고 명시해야 한다.

### 12.7 과거 standalone을 수정 작업에 그대로 복사

소스와 실행 파일 버전이 달라질 수 있다. 모든 revision에서 재생성·재검증해야 한다.

---

## 13. 최종 완료 기준

다음 항목이 모두 충족되면 수정 완료로 판단할 수 있다.

### 라우팅

- [ ] 새 요청은 어떤 키워드가 있어도 유효 파이프라인이 `web-app-lite` 또는 `web-app`이다.
- [ ] 수정 요청은 `feature` 또는 `bugfix`이지만 웹 배포 계약을 유지한다.
- [ ] API와 CLI에서 비웹 파이프라인을 직접 강제할 수 없다.
- [ ] 모바일·데스크톱·CLI 등의 의도는 adaptation profile에 보존된다.

### 구현 계약

- [ ] 모든 프로젝트의 `project.json`에 `delivery.target=web`이 있다.
- [ ] 일반 단계와 repair 단계 모두 웹 배포 계약을 받는다.
- [ ] Flutter·Electron·네이티브 전용 결과가 최종 제품으로 생성되지 않는다.

### 단일 HTML

- [ ] `00_START_HERE.html`이 생성된다.
- [ ] 필수 CSS·JS·이미지·폰트가 내장된다.
- [ ] 핵심 흐름에 외부 네트워크가 필요하지 않다.
- [ ] `file://` 실제 브라우저 테스트가 통과한다.
- [ ] 콘솔 치명 오류와 미해결 자산이 없다.
- [ ] 비밀정보가 없다.
- [ ] 서버 의존 기능은 `local-demo`로 정직하게 표시된다.

### 최종 ZIP

- [ ] ZIP 루트에 `00_START_HERE.html`이 있다.
- [ ] ZIP 루트에 `README_FIRST.md`, `DELIVERY.json`이 있다.
- [ ] 전체 소스는 `source/`에 있다.
- [ ] manifest 해시와 실제 HTML 해시가 일치한다.
- [ ] ZIP 경로 순회·심볼릭 링크·비밀 파일이 없다.
- [ ] 산출물 검증 실패 시 다운로드가 활성화되지 않는다.

### UX

- [ ] 완료 화면의 첫 버튼이 “바로 실행 HTML 받기”이다.
- [ ] ZIP 버튼은 “전체 웹앱 ZIP 받기”로 표시된다.
- [ ] 압축 해제 후 어떤 파일을 열어야 하는지 즉시 알 수 있다.
- [ ] `full`과 `local-demo` 모드가 사용자에게 구분되어 보인다.

---

## 14. 최종 권장안 요약

가장 안전한 구조는 다음과 같다.

```text
원문 요청
  ↓
요청 형태 분류: mobile / desktop / cli / api / ...
  ↓
웹 UX 적응 프로필 생성
  ↓
유효 작업 파이프라인 선택: web-app-lite / web-app
  ↓
전체 웹앱 구현·테스트
  ↓
standalone 전용 빌드
  ↓
00_START_HERE.html 생성
  ↓
file:// + 무네트워크 브라우저 검증
  ↓
루트 HTML + 안내 + manifest + source/ 패키징
  ↓
아카이브 재검증 후에만 완료
```

즉, **“웹앱으로 만들어 달라”는 모델 지시가 아니라 `project delivery contract + deterministic tools + required gates + archive validation`의 조합으로 강제**해야 한다. 이 방식이라야 입력 명령과 무관하게 항상 웹앱이 만들어지고, 초보 사용자도 압축을 푼 뒤 `00_START_HERE.html`만 더블클릭하여 바로 확인할 수 있다.
