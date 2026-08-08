# AppForge-LLM — LLM API 키 보안 집중 검토

검토 대상: `AppForge-LLM-main(1)(1).zip`  
검토 방식: 압축 해제 후 정적 데이터 흐름 분석, 일반적인 비밀정보 패턴 검사, 일부 Python 보안 테스트 및 격리 동작 검증  
검토 초점: API 키·OAuth 토큰의 입력, 전송, 저장, 사용, 로그, 생성 코드 실행, 최종 ZIP 유출 경로

---

## 1. 최종 판정

**보안 우려가 있습니다. 현재 상태를 인터넷·LAN·다중 사용자 환경에 배포하는 것은 권장하지 않으며, 개인 로컬 실행에서도 P0 항목을 먼저 수정하는 편이 안전합니다.**

좋은 점도 있습니다.

- 제출 ZIP 478개 엔트리를 일반적인 OpenAI/Anthropic/Google/GitHub/AWS 키 및 개인키 패턴으로 검사했으나 실제 키로 판단되는 값은 발견되지 않았습니다.
- 정상 상태 응답은 저장된 API 키 원문을 브라우저에 반환하지 않습니다.
- OAuth 공개 응답도 access/refresh token을 제거합니다.
- 프로바이더 오류의 인증 헤더·본문에는 비밀값 치환 로직이 있습니다.
- 생성 프로젝트 명령에는 호스트의 API 키 환경변수가 직접 상속되지 않습니다.

그러나 다음 세 경로는 높은 우선순위로 수정해야 합니다.

1. **비신뢰 생성 코드가 API 키를 가진 프로세스와 같은 OS 사용자·호스트에서 실행됩니다.** 기본 파일 저장 키는 직접 읽을 수 있습니다.
2. **LLM 브리지에 인증이 없고 연결 테스트가 호출자 지정 Base URL과 기존 저장 키를 결합합니다.** 키체인·환경변수·OAuth 키도 외부 주소로 전송시킬 수 있습니다.
3. **외부 모델 카탈로그가 프로바이더의 인증 환경변수 이름과 API 목적지까지 결정합니다.** 카탈로그 공급망 침해가 자격증명 탈취로 이어질 수 있습니다.

---

## 2. 위협 경계

보호할 자산은 다음과 같습니다.

- 파일·키체인·환경변수에 저장된 LLM API 키
- OAuth access token 및 refresh token
- 프로바이더 계정의 과금 한도와 사용 권한
- AppForge 웹 세션 토큰

주요 공격자는 다음과 같습니다.

- 생성되거나 가져온 프로젝트의 악성 빌드·테스트·설치 스크립트
- 악성 또는 침해된 패키지 의존성
- 같은 사용자 권한으로 실행되는 로컬 프로세스
- 로컬 브리지로 요청을 유도하는 웹 콘텐츠
- 브리지가 외부 주소에 바인딩된 경우 네트워크 공격자
- 외부 모델 카탈로그 또는 그 캐시의 공급망 침해

---

## 3. 주요 발견 사항

## P0-1. 비신뢰 프로젝트 코드가 비밀정보와 같은 OS 권한으로 실행됨

**위험도: 높음**  
**영향: 저장 API 키·OAuth 토큰 탈취, 외부 유출, 과금 악용**

### 근거

- 기본 비밀 저장소는 파일입니다: `llm_bridge/src/config.ts:42-45`.
- 파일 백엔드는 `apiKey`와 `oauth` 값을 JSON에 그대로 직렬화합니다: `llm_bridge/src/config.ts:158-167`.
- 기본 위치는 `~/.appforge/llm/providers.json`입니다: `llm_bridge/src/config.ts:32-40`.
- 명령 실행 환경은 API 키 환경변수는 제거하지만 `HOME`을 유지합니다: `appforge/tooling/command.py:36-45`, `199-206`.
- 테스트·빌드는 단순히 같은 사용자 권한의 호스트 subprocess로 실행됩니다: `appforge/tooling/tools/quality.py:43-64`, `appforge/tooling/command.py:209-217`.
- 의존성 설치도 동일한 방식이며 npm/pnpm/yarn의 lifecycle script를 차단하지 않습니다: `appforge/tooling/tools/execution.py:69-130`.
- 프로젝트 코드가 OS가 허용한 파일·프로세스·네트워크에 접근할 수 있다는 사실을 문서도 인정합니다: `docs/SAFETY.md:27-29`, `34-36`.

더미 구성 파일을 사용한 실행 검증에서, `run_command`로 실행된 작업공간 스크립트가 `HOME` 아래 작업공간 밖의 `providers.json`을 읽을 수 있음을 확인했습니다.

### 실제 공격 경로

1. 모델 출력, 기존 저장소, 또는 패키지 의존성이 악성 build/test/install script를 포함합니다.
2. AppForge가 이를 같은 OS 사용자로 실행합니다.
3. 스크립트가 `~/.appforge/llm/providers.json` 또는 브리지 `/health`가 알려주는 구성 경로를 읽습니다.
4. 키를 외부로 전송하거나 로그·생성 파일·최종 ZIP에 삽입합니다.

`0600` 권한은 다른 OS 사용자를 막을 뿐, **같은 사용자로 실행되는 생성 코드에는 보호 효과가 없습니다.**

### 수정안

- install/test/build/run_command를 **별도 격리 실행기**에서 수행해야 합니다.
- 격리 실행기는 별도 UID, 임시 `HOME`, 작업공간만 읽기·쓰기 마운트, `~/.appforge` 미마운트, 호스트 PID/IPC 미공유를 적용해야 합니다.
- 기본적으로 호스트 loopback과 브리지 포트·소켓에 접근하지 못하게 해야 합니다.
- 의존성 설치는 가능한 경우 lifecycle script를 기본 차단하고, 필요한 스크립트는 격리 환경에서만 허용해야 합니다.
- AppForge 전체와 키를 한 컨테이너에 같이 넣는 방식은 충분하지 않습니다. **생성 코드 실행 컨테이너와 비밀정보·브리지 프로세스를 분리**해야 합니다.

---

## P0-2. 인증 없는 브리지와 Base URL 재정의가 결합되어 저장 키를 외부로 전송 가능

**위험도: 높음 — 외부/LAN 바인딩 시 치명적**  
**영향: API 키·OAuth access token 직접 탈취, 무단 과금, 설정 삭제·변조**

### 근거

- 브리지의 provider, generate, test, OAuth, agent 관리 라우트에는 인증 미들웨어가 없습니다: `llm_bridge/src/server.ts:51-70`, `814-833`.
- 연결 테스트는 요청의 `baseURL`을 받아들이면서, 요청에 키가 없으면 기존 저장 키를 사용합니다: `llm_bridge/src/server.ts:372-386`.
- 키 해석 우선순위는 OAuth → 저장 키 → 환경변수이고, Base URL은 저장·요청값을 그대로 사용합니다: `llm_bridge/src/registry.ts:375-393`, `444-494`.
- OpenAI 구현은 선택된 Base URL로 요청하면서 키를 Bearer 인증 헤더에 넣습니다: `llm_bridge/vendor/llm/providers/openai.ts:24-35`, `llm_bridge/vendor/llm/route/auth.ts:43-48`.
- 브리지 호스트는 환경변수로 외부 인터페이스에 바꿀 수 있습니다: `llm_bridge/src/server.ts:20-21`, `843-860`.
- Python 클라이언트는 원격 `http://` 브리지도 허용하며 인증 헤더를 사용하지 않습니다: `appforge/llm_bridge.py:75-104`.
- 브리지 URL은 검증 없이 환경변수에서 수용됩니다: `appforge/web_jobs.py:155-178`.
- 요청 본문은 Content-Type을 확인하지 않고 JSON으로 파싱합니다: `llm_bridge/src/server.ts:91-101`.

### 실제 공격 경로

1. 악성 생성 코드나 로컬 프로세스가 인증 없이 `/providers`를 호출하여 키가 설정된 프로바이더를 찾습니다.
2. `/providers/{id}/test`에 공격자 서버의 Base URL을 넣습니다.
3. 브리지는 기존 저장 키·OAuth access token·환경변수 키를 불러옵니다.
4. 연결 테스트 요청의 인증 헤더나 쿼리에 해당 비밀값을 넣어 공격자 서버로 전송합니다.

CORS가 응답 읽기를 제한하더라도 서버 측 인증을 대신하지 않습니다. 브라우저의 교차 출처 정책에 의존하지 말아야 하며, 로컬 프로젝트 코드와 로컬 프로세스에는 CORS 자체가 적용되지 않습니다.

### 수정안

- Python 프로세스가 매 실행마다 고엔트로피 브리지 토큰을 생성하고, 브리지의 모든 민감 라우트가 `Authorization: Bearer` 또는 전용 헤더를 필수로 검증해야 합니다.
- 토큰은 브라우저와 생성 코드 subprocess에 전달하지 않아야 합니다.
- `/healthz`는 최소한의 `ok/version`만 반환하고 구성 경로·활성 프로바이더 정보는 인증 후에만 반환해야 합니다.
- built-in provider의 Base URL은 고정하거나 엄격한 HTTPS 호스트 allowlist를 사용해야 합니다.
- `/providers/{id}/test`는 **기존 키와 호출자 제공 Base URL을 절대로 결합하지 않아야 합니다.** 임시 Base URL 테스트가 필요하면 임시 전용 키도 같은 요청에서 명시적으로 받아야 하며, custom provider에만 허용해야 합니다.
- `Origin`, `Host`, `Sec-Fetch-Site`, `Content-Type: application/json` 검증을 추가해야 합니다.
- 비루프백 HTTP는 거부하고, 원격 브리지가 꼭 필요하면 HTTPS + 상호 인증 또는 강한 토큰 인증을 필수화해야 합니다.
- 요청 수·동시성·비용 한도를 브리지에서도 제한해야 합니다.

---

## P0-3. 외부 모델 카탈로그가 키의 출처와 전송 목적지를 제어함

**위험도: 높음(공급망)**  
**영향: 카탈로그 또는 캐시 침해 시 API 키뿐 아니라 다른 환경변수 비밀값까지 외부 전송 가능**

### 근거

- 브리지는 기본적으로 `https://models.dev/api.json`을 주기적으로 가져오며 서명 검증 없이 JSON 객체만 확인합니다: `llm_bridge/src/catalog.ts:16-19`, `79-109`.
- 카탈로그의 `env[0]`이 프로바이더 인증 환경변수 이름이 되고, `api`가 기본 Base URL이 됩니다: `llm_bridge/src/registry.ts:119-141`.
- 카탈로그가 존재하면 정적 내장 정의보다 우선합니다: `llm_bridge/src/registry.ts:315-324`.
- 브리지는 해당 환경변수를 읽고 카탈로그 Base URL로 인증 요청을 만듭니다: `llm_bridge/src/registry.ts:375-393`, `469-494`.
- 자동 시작된 브리지는 전체 호스트 환경을 복사해서 받습니다: `appforge/llm_bridge_process.py:182-195`.

HTTPS는 전송 중 변조 위험을 낮추지만, 카탈로그 서비스·캐시·설정된 대체 URL이 침해되었을 때의 피해를 막지 못합니다.

### 수정안

- 원격 카탈로그는 모델 ID·표시명·가격 등 **비보안 메타데이터만** 제공하게 해야 합니다.
- built-in provider의 `env_key`, API endpoint, 인증 방식, provider 구현 매핑은 로컬 소스에 고정해야 합니다.
- 카탈로그가 기존 프로바이더의 endpoint나 환경변수 이름을 덮어쓰지 못하게 해야 합니다.
- 동적 endpoint가 불가피하면 서명 검증, 스키마 검증, provider별 endpoint allowlist, HTTPS 강제, 캐시 무결성 검증이 필요합니다.
- 브리지 subprocess에는 전체 `os.environ` 대신 실제 필요한 환경변수만 allowlist로 전달해야 합니다.

---

## P1-1. 기본 파일 저장이 평문이며 권한 설정이 write 후·fail-open 방식

**위험도: 보통**  
**영향: 다른 로컬 사용자, 공유 파일시스템, 백업·동기화, 권한 설정 실패 시 키 노출**

### 근거

- 기본 백엔드는 `file`입니다: `llm_bridge/src/config.ts:42-45`.
- API 키와 OAuth 객체가 평문 JSON으로 저장됩니다: `llm_bridge/src/config.ts:158-167`.
- 디렉터리 생성 시 `0700`을 강제하지 않습니다: `llm_bridge/src/config.ts:56-58`.
- 파일을 먼저 쓴 뒤 `chmod 0600`을 적용합니다: `llm_bridge/src/config.ts:221-226`.
- `chmod` 실패는 모두 무시합니다: `llm_bridge/src/config.ts:60-65`.
- 문서는 파일이 `0600`이라고 단정합니다: `docs/SAFETY.md:23-25`, `docs/WEB_APP.md:232`.

새 파일은 잠시 프로세스 umask에 따른 권한으로 생성될 수 있으며, chmod가 실패하는 파일시스템에서는 넓은 권한이 계속 유지될 수 있습니다. 직접 write는 중단 시 파일 손상 가능성도 있습니다.

### 수정안

- macOS에서는 OS Keychain을 기본값으로 전환합니다.
- 파일 fallback은 디렉터리 `0700`, 파일 `0600`을 생성 시점부터 강제합니다.
- `open(..., mode=0600, O_CREAT|O_EXCL)` 또는 동등한 안전한 임시 파일을 사용해 fsync 후 atomic rename 합니다.
- 파일이 심볼릭 링크가 아닌 정규 파일인지, 현재 사용자 소유인지, 권한이 안전한지 확인합니다.
- 권한 적용 실패 시 저장을 거부하는 fail-closed 정책을 사용합니다.
- `APPFORGE_DATA_DIR`과 비밀 저장 경로를 분리하고 비밀 경로는 절대경로로 정규화합니다.

---

## P1-2. 웹 세션 토큰이 URL과 로그에 노출됨

**위험도: 보통**  
**영향: 토큰을 읽은 로컬 사용자·로그 수집기가 provider 설정·테스트·OAuth·작업 API를 재사용 가능**

### 근거

- 시작 URL에 세션 토큰을 넣고 전체 URL을 로그에 기록합니다: `appforge/web.py:678-684`.
- 최초 로드 후 URL에서 제거하고 sessionStorage에 보관하는 처리는 적절합니다: `frontend/src/api.ts:35-48`.
- 그러나 SSE와 다운로드 요청은 다시 master token을 query string에 넣습니다: `frontend/src/App.vue:188-198`, `frontend/src/components/JobPanel.vue:59-65`.
- 서버도 해당 query token을 허용합니다: `appforge/web.py:245-258`.

### 수정안

- 전체 토큰 URL을 로그에 남기지 않습니다.
- one-time bootstrap code로 `HttpOnly`, `SameSite=Strict`, `Secure` 조건부 쿠키를 발급하는 방식이 적절합니다.
- SSE에는 master token 대신 짧은 TTL·단일 작업 범위의 일회성 ticket을 사용합니다.
- 다운로드는 인증된 fetch 후 Blob 저장 방식 또는 짧은 수명의 단일 다운로드 ticket을 사용합니다.
- 세션 종료 시 토큰을 즉시 폐기·회전합니다.

---

## P1-3. 최종 ZIP 비밀정보 검사가 불완전하고 일부 파이프라인은 검사 자체가 없음

**위험도: 보통**  
**영향: 생성 소스에 들어간 API 키가 최종 ZIP으로 전달될 수 있음**

### 근거

- 비밀정보 정규식은 개인키, AWS, GitHub, 따옴표가 있는 일반 credential assignment 정도만 검사합니다: `appforge/tooling/tools/security.py:28-33`.
- 2MB 초과 파일과 binary 파일은 건너뜁니다: `appforge/tooling/tools/security.py:43-55`.
- 실제 더미 검사에서 따옴표가 있는 `api_key` 한 형식만 잡고, unquoted 환경변수형, 문서의 bare token, `.npmrc` auth token 형식을 놓쳤습니다.
- `prototype` 파이프라인은 demo 다음 바로 handoff/archive로 이동하며 secret scan 단계가 없습니다: `appforge/resources/pipeline_defs/prototype.yaml:218-267`.
- 아카이버는 일부 이름·확장자만 제외하며, ZIP을 만들기 직전에 내용 검사를 다시 하지 않습니다: `appforge/tooling/tools/release.py:69-105`.
- `.npmrc`, `.pypirc`, `.netrc`, `auth.json`, 클라우드 SDK credential 파일 등은 일반적으로 제외되지 않습니다.

### 수정안

- 모든 파이프라인, 특히 `prototype`의 handoff 전에 final secret scan을 필수화합니다.
- `ArchiveWorkspaceTool`이 실제 ZIP 대상 파일 목록을 즉시 재검사하고 실패 시 아카이브를 만들지 않도록 합니다.
- OpenAI/Anthropic/Google 등 provider별 패턴, unquoted assignment, JSON/YAML/TOML, auth 파일명, JWT, 고엔트로피 검사를 추가합니다.
- 가능하면 검증된 전용 secret scanner를 오프라인으로 통합하고 결과에는 비밀값 원문을 절대 기록하지 않습니다.
- 생성 후 ZIP 내부를 다시 열어 manifest와 내용 검사를 수행합니다.

---

## P1-4. macOS Keychain 저장 시 비밀값이 프로세스 argv에 포함됨

**위험도: 보통~낮음**  
**영향: 프로세스 관찰·진단 정보·실패 오류 메시지에 키가 일시 노출될 수 있음**

### 근거

- `security add-generic-password` 호출의 `-w` 다음 argv에 API 키 또는 OAuth JSON을 직접 넣습니다: `llm_bridge/src/config.ts:112-118`.
- 일반적인 Node `execFile` 실패 오류에는 실행 명령과 인자가 포함될 수 있습니다.
- 브리지 dispatch는 예기치 않은 오류의 `message`를 그대로 응답합니다: `llm_bridge/src/server.ts:823-830`.
- Python 웹도 BridgeError 메시지와 payload를 브라우저에 그대로 반환합니다: `appforge/web.py:491-509`.

### 수정안

- 가능한 경우 macOS Security framework 또는 검증된 native keychain 라이브러리를 사용합니다.
- 비밀값을 command-line argument에 넣지 않습니다.
- child-process 오류는 일반화하고, 명령·argv·stderr를 반환하기 전에 비밀정보를 강제 치환합니다.
- `/usr/bin/security`처럼 실행 파일 경로를 고정해 PATH 하이재킹 가능성도 줄입니다.

---

## P2-1. 키를 생략한 provider 설정 저장이 기존 키를 삭제하는 의미 불일치

**위험도: 낮음(무결성·가용성)**

- 프런트엔드는 새 키가 입력된 경우에만 `apiKey`를 보냅니다: `frontend/src/components/ProviderSettings.vue:360-371`.
- 브리지 route는 누락된 `apiKey`를 `null`로 변환합니다: `llm_bridge/src/server.ts:335-344`.
- 저장 함수 주석은 null/empty가 기존 유지라고 설명하지만 실제 코드는 null과 빈 문자열을 삭제로 처리합니다: `llm_bridge/src/config.ts:238-260`.

따라서 모델 또는 Base URL만 저장해도 기존 file/keychain API 키가 지워질 수 있습니다. 명시적인 `clearApiKey: true` 동작을 분리하고, 누락은 반드시 기존 유지로 처리해야 합니다.

---

## 4. 확인된 보호 장치

- Provider status는 `has_key`, `key_source`만 반환하고 원문은 반환하지 않습니다: `llm_bridge/src/registry.ts:407-433`.
- OAuth 공개 응답은 access/refresh token을 제외합니다: `llm_bridge/src/oauth/public.ts:18-44`.
- 프로바이더 HTTP 오류는 민감한 header/query/body field 및 실제 요청 비밀값을 치환합니다: `llm_bridge/vendor/llm/route/executor.ts:41-75`, `166-201`.
- UI API 키 필드는 password type이며 저장 후 draft를 비웁니다: `frontend/src/components/ProviderSettings.vue:360-372`, `667-675`.
- Python 웹 계층은 loopback Host, 교차 출처 mutation, 세션 토큰을 검사합니다: `appforge/web.py:221-261`.
- 생성 preview는 sandbox와 `connect-src 'none'` 정책을 사용합니다: `appforge/web.py:171-190`.
- 생성 명령 subprocess는 호스트 API 키 환경변수를 상속하지 않습니다: `appforge/tooling/command.py:36-45`, `199-206`. 관련 Python 테스트 4개를 선택 실행하여 통과를 확인했습니다.

이 장치들은 유효하지만, 같은 사용자 파일 접근과 무인증 브리지 접근을 차단하지는 못합니다.

---

## 5. 권장 수정 순서

### P0 — 사용 확대 전에 필수

1. 생성 코드 install/test/build를 별도 UID·별도 컨테이너/VM에서 실행하고 `~/.appforge`, host loopback, 브리지 포트·소켓 접근을 차단합니다.
2. 브리지 전 라우트에 강한 프로세스 간 인증을 추가합니다.
3. stored/env/OAuth credential과 호출자 제공 Base URL을 결합하지 못하게 합니다.
4. built-in provider endpoint와 env mapping을 로컬에 고정하고 외부 카탈로그에서는 모델 메타데이터만 사용합니다.
5. 비루프백 HTTP bridge를 거부합니다.

### P1 — 같은 릴리스에서 권장

1. macOS Keychain 기본화, 파일 fallback atomic `0600`/directory `0700`/fail-closed.
2. master web token의 로그·query string 사용 제거.
3. 모든 파이프라인의 최종 ZIP 직전 secret scan.
4. keychain argv 전달과 raw child-process error 반환 제거.
5. 브리지 subprocess 환경변수를 allowlist로 축소.

### P2 — 안정화

1. provider update의 누락/삭제 semantics 분리.
2. 구성 파일·캐시의 소유권, symlink, 무결성 검사.
3. 인증·Base URL·파일 mode·archive secret scan에 대한 회귀 테스트 추가.

---

## 6. 수정 전 임시 운영 수칙

- 브리지와 웹 서버를 반드시 `127.0.0.1`에만 바인딩합니다.
- `APPFORGE_LLM_BRIDGE_URL`에 원격 `http://` 주소를 사용하지 않습니다.
- built-in provider의 Base URL을 변경하지 않습니다.
- AppForge 전용의 별도 API 키를 사용하고 최소 권한·낮은 사용 한도를 설정합니다.
- macOS에서는 at-rest 보호를 위해 `APPFORGE_LLM_SECRET_BACKEND=keychain`을 사용하되, 이것만으로 무인증 브리지 문제는 해결되지 않는다는 점을 전제로 합니다.
- 신뢰하지 않는 저장소·패키지·프롬프트는 현재 호스트에서 자동 install/test/build하지 않습니다.
- 이미 이 버전을 외부/LAN에 노출했거나 불신 코드와 함께 실행했다면 해당 provider 키를 폐기·재발급하고 사용 내역을 확인합니다.
- 프롬프트나 생성 소스에 실제 키를 직접 넣지 않습니다.

---

## 7. 검토 한계

- 현재 제공된 소스 스냅샷만 검토했으며 Git 과거 이력, 실제 `~/.appforge/llm/providers.json`, 운영 로그, provider 사용 내역은 포함하지 않았습니다.
- 원본 ZIP의 비밀정보 검사는 일반적인 패턴 기반이며 모든 키 형식을 증명적으로 배제할 수는 없습니다.
- Bun이 설치되어 있지 않아 TypeScript/Bun 테스트 스위트는 실행하지 못했고 해당 부분은 소스 데이터 흐름으로 검증했습니다.
- Python의 웹 보안 가드 및 환경변수 비상속 관련 선택 테스트는 통과했습니다.
