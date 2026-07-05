# AppForge-LLM v6

**프롬프트만 입력하면 AI 에이전트가 앱을 기획·구현·검증하고, 프리뷰와 소스 ZIP으로 완성해 주는 앱 제작 서비스입니다.**

[English](README.md) · [v5 에이전트 엔지니어링](docs/V5_AGENTIC_ENGINEERING.md) · [웹앱 상세](docs/WEB_APP.md) · [아키텍처](docs/ARCHITECTURE.md) · [안전 모델](docs/SAFETY.md) · [에이전트 실행 규약](AGENTS.md)

AppForge-LLM v6는 통제된 app-building agent pipeline입니다. 사용자가 원하는 앱을 설명하면 요구사항을 정리하고, 코드를 작성하고, 테스트·빌드·보안 게이트를 실행하며, 실패 시 수리 루프를 거친 뒤 프리뷰 가능한 소스 ZIP으로 패키징합니다. 자동 파이프라인 라우팅, 격리된 프로젝트 준비, 산출물 검증, 릴리스 점검, 안전한 압축과 검증된 ZIP 다운로드는 계속 서버가 소유합니다.

```text
앱 설명 입력 → AI 에이전트가 기획·구현·검증 → 프리뷰 → 소스 ZIP 다운로드
```

## v6 변경점

- **도구 사용 브릿지 에이전트:** `llm-bridge-agent`가 구현·검증 단계에서 안전한 읽기/쓰기/검색/테스트/빌드 도구를 사용하며, 파일·명령 실행 권한은 Python 러너에 남깁니다.
- **대상 수리 루프:** 게이트 실패 시 실패 로그와 관련 파일 본문을 좁혀 수리 프롬프트로 재시도하고, 반복 실패 시 전략 전환으로 넘어갑니다.
- **가짜 성공 방지:** 브릿지 자동 stage-result 체크는 검증 통과가 아니라 `unverified-self-report`로 표시됩니다.
- **실시간 웹 루프:** 작업 SSE, 워크스페이스 트리/파일, 아티팩트 조회, 정적 프리뷰 빌드/iframe 경로를 추가했고 Vue UI는 EventSource와 폴링 fallback을 함께 사용합니다.
- **경량 라우팅 트랙:** 작은 웹앱 요청은 `web-app-lite`로 라우팅되어 초기 설계를 `engineering_spec` 하나로 접고 구현으로 넘어갈 수 있습니다.

## 가장 쉬운 실행 방법

필수 조건은 Python 3.11 이상, 로컬 LLM 브릿지용 [Bun](https://bun.sh), 그리고 외부 LLM 프로바이더(OpenAI · Anthropic · Google · OpenRouter · DeepSeek · Groq 등)의 API 키입니다. 코딩 에이전트 CLI(Codex/Claude)는 더 이상 사용하지 않습니다.

빌드된 wheel에서 실행:

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install dist/openappforge-0.6.0-py3-none-any.whl

appforge web
```

소스에서 실행:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm --prefix frontend install
npm --prefix frontend run build

appforge web
```

기본 브라우저에서 `http://127.0.0.1:8787`이 열립니다. 브라우저에는 현재 작업 ID만 저장되고, 실제 작업 상태는 `.appforge-web/jobs/`에 저장됩니다.

## 프론트엔드 개발

Python API 서버와 Vite 개발 서버를 각각 실행합니다.

```bash
appforge web --no-browser
npm --prefix frontend run dev
```

`http://127.0.0.1:5173`을 열면 됩니다. Vite는 `/api` 요청을 로컬 FastAPI 서버 `http://127.0.0.1:8787`로 프록시합니다.

운영용 프론트엔드 빌드:

```bash
npm --prefix frontend run build
```

빌드 결과는 `appforge/resources/web/index.html`, `appforge/resources/web/assets/*`, `favicon.svg`, `manifest.webmanifest`에 기록됩니다.

## 외부 LLM 연결 설정

AppForge-LLM v6는 파이프라인의 모든 단계를 로컬 LLM 브릿지(`llm_bridge/`)를 거쳐 **외부 LLM API**로만 실행합니다. Codex CLI / Claude Code CLI / 사용자 정의 명령(`APPFORGE_AGENT_CMD`) 드라이버는 제거되었습니다.

1. 브릿지 의존성 설치: `cd llm_bridge && bun install`
2. `appforge web` 실행 — 웹앱이 loopback 브릿지를 자동으로 시작합니다(또는 `bun run dev`로 직접 실행).
3. 웹앱 상단의 LLM 연결 설정 패널에서 프로바이더·API 키·모델을 선택합니다.
4. 선택한 프로바이더/모델이 활성화되면 준비 완료 상태가 되고 파이프라인을 시작할 수 있습니다.

### 주요 환경 변수

```text
APPFORGE_PROJECTS_DIR          생성 프로젝트 경로, 기본값 projects/
APPFORGE_DATA_DIR              웹 작업 상태 경로, 기본값 .appforge-web/
APPFORGE_DRIVER                llm-bridge-agent 기본값, auto는 동일한 브릿지 에이전트 경로 별칭
APPFORGE_MODEL                 브릿지에 전달할 모델 이름 (선택)
APPFORGE_LLM_BRIDGE_URL        FastAPI→브릿지 URL, 기본값 http://127.0.0.1:8788
APPFORGE_LLM_PROVIDER          활성 프로바이더 덮어쓰기 (선택)
APPFORGE_LLM_BRIDGE_AUTOSTART  appforge web의 loopback 브릿지 자동 시작, 기본값 true
APPFORGE_START_LLM_BRIDGE      build.sh 런처가 브릿지를 시작/재사용하도록 요청
APPFORGE_SKIP_LLM_BRIDGE       appforge web/build.sh의 브릿지 자동 시작 비활성화
APPFORGE_ALLOW_NETWORK         기본값 false, true로 설정하면 패키지 설치·원격 감사 허용
APPFORGE_STAGE_TIMEOUT         단계별 제한 시간(초), 기본값 3600
APPFORGE_MAX_STAGE_ATTEMPTS    단계별 최대 자동 시도 횟수
APPFORGE_PROMPT_MAX_CHARS      요청 입력 제한, 기본값 20000
```

> 참고: `codex`, `claude`, `generic`을 `APPFORGE_DRIVER`에 지정하면 거부됩니다. 외부 LLM 프로바이더 API 키만 지원합니다.

## 기존 CLI 호환성

v6의 권장 UX는 `appforge web`이지만 기존 CLI와 에이전트 네이티브 흐름도 유지됩니다.

```bash
appforge forge "반응형 개인 예산 웹앱을 만들어라" --driver auto --allow-network
appforge run <project>
appforge status <project>
appforge prompt <project>
appforge complete <project>
appforge doctor
appforge tool list
```

기존 저장소 변경은 CLI에서 계속 지원합니다.

```bash
cd existing-project
appforge forge "기존 로그인을 유지하면서 패스키 로그인을 추가하라" \
  --target . --driver auto --allow-network
```

## 개발 및 검증

```bash
python -m pip install -e '.[dev]'
npm --prefix frontend install
npm --prefix frontend run build
python -m compileall -q appforge tests
python -m pytest
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

## 라이선스와 영감

AppForge-LLM v6/OpenAppForge는 Apache License 2.0으로 제공되는 독립적인 원본 구현입니다. 선언형 파이프라인, 조합 가능한 스킬, 자동 발견 도구, 체크포인트와 리뷰 게이트를 저장소 중심으로 결합하는 접근은 OpenMontage에서 영감을 받았습니다. OpenMontage 소스 코드는 포함하지 않았습니다. 자세한 내용은 [ATTRIBUTION.md](ATTRIBUTION.md)에 기록했습니다.
