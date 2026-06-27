# AppForge-LLM v4

**앱 설명 한 번으로 단계별 제작 상태를 확인하고 검증된 소스 ZIP을 다운로드하는 Vite + Vue 웹앱입니다.**

[English](README.md) · [v4 엔지니어링](docs/V4_ENGINEERING.md) · [웹앱 상세](docs/WEB_APP.md) · [아키텍처](docs/ARCHITECTURE.md) · [안전 모델](docs/SAFETY.md) · [에이전트 실행 규약](AGENT_GUIDE.md)

AppForge-LLM v4는 v3의 Vite + Vue 웹 UX 위에 **Specification → Workflow → Memory → Loop Engineering** 축을 강제하는 제작 파이프라인을 추가합니다. 자동 파이프라인 라우팅, 격리된 프로젝트 준비, 코딩 에이전트 실행, 산출물 검증, 테스트·빌드·보안 게이트, 릴리스 점검, 안전한 압축과 검증된 ZIP 다운로드는 계속 서버가 소유합니다. v4의 핵심 변화는 앱을 바로 구현하기 전에 명세, 흐름, 상태 기억, 반복 루프를 각각 독립 산출물과 검증 가능한 계약으로 단단히 고정하는 것입니다.

```text
앱 설명 입력 → 앱 만들기 → 실시간 단계 확인 → 완료된 소스 ZIP 다운로드
```

## v4 변경점

- **4단계 엔지니어링 스파인:** 모든 주요 파이프라인에 `specification → workflow_design → memory_engineering → loop_engineering` 구간을 추가하거나 강화했습니다.
- **강화된 산출물 스키마:** `requirements_spec`와 `workflow_spec`를 더 엄격하게 만들고, `memory_spec`, `loop_spec` 스키마를 새로 추가했습니다.
- **영속 엔지니어링 메모리:** 실행기가 각 단계의 성공·실패, 검증 결과, 결정사항을 `.appforge/memory/stage-memory.jsonl`에 남기고 다음 단계 프롬프트에 요약합니다.
- **반복 실패 루프 가드:** 같은 실패 서명이 반복되면 `REPEATED_FAILURE_LOOP`로 감지해 무의미한 자동 재시도를 멈추고 수정 전략 변경을 요구합니다.
- **웹 상태 표시 보강:** 새 Memory/Loop 단계를 한국어 상태 타임라인에 표시하고, 실패 루프 감지 오류를 사용자에게 설명합니다.

## 가장 쉬운 실행 방법

필수 조건은 Python 3.11 이상, 로컬 LLM 브릿지용 [Bun](https://bun.sh), 그리고 외부 LLM 프로바이더(OpenAI · Anthropic · Google · OpenRouter · DeepSeek · Groq 등)의 API 키입니다. 코딩 에이전트 CLI(Codex/Claude)는 더 이상 사용하지 않습니다.

빌드된 wheel에서 실행:

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install dist/openappforge-0.4.0-py3-none-any.whl

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
appforge web --no-open-browser
npm --prefix frontend run dev
```

`http://127.0.0.1:5173`을 열면 됩니다. Vite는 `/api` 요청을 로컬 FastAPI 서버 `http://127.0.0.1:8787`로 프록시합니다.

운영용 프론트엔드 빌드:

```bash
npm --prefix frontend run build
```

빌드 결과는 `appforge/resources/web/index.html`, `appforge/resources/web/assets/*`, `favicon.svg`, `manifest.webmanifest`에 기록됩니다.

## 외부 LLM 연결 설정

AppForge-LLM v4는 파이프라인의 모든 단계를 로컬 LLM 브릿지(`llm_bridge/`)를 거쳐 **외부 LLM API**로만 실행합니다. Codex CLI / Claude Code CLI / 사용자 정의 명령(`APPFORGE_AGENT_CMD`) 드라이버는 제거되었습니다.

1. 브릿지 의존성 설치: `cd llm_bridge && bun install`
2. `appforge web` 실행 — 웹앱이 loopback 브릿지를 자동으로 시작합니다(또는 `bun run dev`로 직접 실행).
3. 웹앱 상단의 LLM 연결 설정 패널에서 프로바이더·API 키·모델을 선택합니다.
4. 선택한 프로바이더/모델이 활성화되면 준비 완료 상태가 되고 파이프라인을 시작할 수 있습니다.

### 주요 환경 변수

```text
APPFORGE_PROJECTS_DIR          생성 프로젝트 경로, 기본값 projects/
APPFORGE_DATA_DIR              웹 작업 상태 경로, 기본값 .appforge-web/
APPFORGE_DRIVER                auto | llm-bridge (모두 동일한 외부 LLM 경로, 기본값 auto)
APPFORGE_MODEL                 브릿지에 전달할 모델 이름 (선택)
APPFORGE_LLM_BRIDGE_URL        FastAPI→브릿지 URL, 기본값 http://127.0.0.1:8788
APPFORGE_LLM_PROVIDER          활성 프로바이더 덮어쓰기 (선택)
APPFORGE_LLM_BRIDGE_AUTOSTART  브릿지 자동 시작, 기본값 true
APPFORGE_ALLOW_NETWORK         기본값 true, 패키지 설치·원격 감사 허용
APPFORGE_STAGE_TIMEOUT         단계별 제한 시간(초), 기본값 3600
APPFORGE_MAX_STAGE_ATTEMPTS    단계별 최대 자동 시도 횟수
APPFORGE_PROMPT_MAX_CHARS      요청 입력 제한, 기본값 20000
```

> 참고: `codex`, `claude`, `generic`을 `APPFORGE_DRIVER`에 지정하면 거부됩니다. 외부 LLM 프로바이더 API 키만 지원합니다.

## 기존 CLI 호환성

v4의 권장 UX는 `appforge web`이지만 기존 CLI와 에이전트 네이티브 흐름도 유지됩니다.

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

AppForge-LLM v4/OpenAppForge는 Apache License 2.0으로 제공되는 독립적인 원본 구현입니다. 선언형 파이프라인, 조합 가능한 스킬, 자동 발견 도구, 체크포인트와 리뷰 게이트를 저장소 중심으로 결합하는 접근은 OpenMontage에서 영감을 받았습니다. OpenMontage 소스 코드는 포함하지 않았습니다. 자세한 내용은 [ATTRIBUTION.md](ATTRIBUTION.md)에 기록했습니다.
