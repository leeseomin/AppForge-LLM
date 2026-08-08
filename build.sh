#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

MODE="serve"
NO_OPEN=0
WEB_PID=""
BRIDGE_PID=""

APPFORGE_WEB_HOST="${APPFORGE_WEB_HOST:-127.0.0.1}"
APPFORGE_WEB_PORT="${APPFORGE_WEB_PORT:-8787}"
APPFORGE_WEB_PORT_FALLBACK_LIMIT="${APPFORGE_WEB_PORT_FALLBACK_LIMIT:-20}"
APPFORGE_LOG_LEVEL="${APPFORGE_LOG_LEVEL:-info}"
APPFORGE_SMOKE_TIMEOUT="${APPFORGE_SMOKE_TIMEOUT:-30}"
APPFORGE_BRIDGE_TIMEOUT="${APPFORGE_BRIDGE_TIMEOUT:-15}"
APPFORGE_LLM_BRIDGE_URL="${APPFORGE_LLM_BRIDGE_URL:-http://127.0.0.1:8788}"
APPFORGE_RUNTIME_DIR="${APPFORGE_DATA_DIR:-.appforge-web}"

APPFORGE_BIN="$ROOT_DIR/.venv/bin/appforge"
WEB_URL="http://${APPFORGE_WEB_HOST}:${APPFORGE_WEB_PORT}"
case "$APPFORGE_RUNTIME_DIR" in
  /*) APPFORGE_RUNTIME_PATH="$APPFORGE_RUNTIME_DIR" ;;
  *) APPFORGE_RUNTIME_PATH="$ROOT_DIR/$APPFORGE_RUNTIME_DIR" ;;
esac

WEB_LOG="$APPFORGE_RUNTIME_PATH/web-smoke.log"
BRIDGE_LOG="$APPFORGE_RUNTIME_PATH/llm-bridge.log"

export APPFORGE_LLM_BRIDGE_URL

log() {
  printf '[build.sh] %s\n' "$*"
}

die() {
  printf '[build.sh] error: %s\n' "$*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

is_true() {
  case "${1:-}" in
    1 | true | TRUE | yes | YES | on | ON) return 0 ;;
    *) return 1 ;;
  esac
}

usage() {
  cat <<'USAGE'
Usage: ./build.sh [--smoke] [--check] [--no-open] [--help]

Prepare and launch the local AppForge-LLM v7 AI app builder web UI.

Options:
  --smoke     Start appforge web without opening a browser, probe /api/health and /, then stop it.
  --check     Prepare Python/frontend dependencies and exit without starting the web server.
  --no-open   Launch normally but suppress browser opening.
  -h, --help  Show this help.

Environment:
  APPFORGE_WEB_HOST                 Loopback bind host, default 127.0.0.1.
  APPFORGE_WEB_PORT                 Preferred bind port, default 8787.
  APPFORGE_WEB_PORT_FALLBACK_LIMIT  Additional ports to scan upward if busy, default 20.
  APPFORGE_NO_OPEN=1                Suppress browser opening.
  APPFORGE_SKIP_INSTALL=1           Reuse the current .venv instead of syncing Python deps.
  APPFORGE_SKIP_FRONTEND_BUILD=1    Reuse current packaged frontend assets.
  APPFORGE_DRIVER                   Driver path; default llm-bridge-agent. auto is an alias.
  APPFORGE_START_LLM_BRIDGE=1       Request web-process-managed llm_bridge startup.
  APPFORGE_SKIP_LLM_BRIDGE=1        Disable managed bridge startup.
  APPFORGE_LLM_BRIDGE_URL           Bridge URL base, default http://127.0.0.1:8788.
  APPFORGE_LLM_BRIDGE_TOKEN         Shared 32+ character capability for a manual bridge.
  APPFORGE_SMOKE_TIMEOUT            Smoke timeout in seconds, default 30.
  APPFORGE_BRIDGE_TIMEOUT           Bridge startup timeout in seconds, default 15.
USAGE
}

set_mode() {
  local next_mode="$1"
  if [[ "$MODE" != "serve" && "$MODE" != "$next_mode" ]]; then
    die "Choose only one of --smoke or --check."
  fi
  MODE="$next_mode"
}

for arg in "$@"; do
  case "$arg" in
    --smoke)
      set_mode "smoke"
      ;;
    --check)
      set_mode "check"
      ;;
    --no-open)
      NO_OPEN=1
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "Unknown argument: $arg"
      ;;
  esac
done

if is_true "${APPFORGE_NO_OPEN:-}"; then
  NO_OPEN=1
fi

if [[ "$MODE" == "smoke" ]]; then
  NO_OPEN=1
fi

validate_uint() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    die "$name must be a positive integer; got '$value'."
  fi
  local numeric=$((10#$value))
  if (( numeric < 1 )); then
    die "$name must be greater than zero; got '$value'."
  fi
}

validate_uint "APPFORGE_WEB_PORT" "$APPFORGE_WEB_PORT"
WEB_PORT_NUM=$((10#$APPFORGE_WEB_PORT))
if (( WEB_PORT_NUM > 65535 )); then
  die "APPFORGE_WEB_PORT must be between 1 and 65535; got '$APPFORGE_WEB_PORT'."
fi
validate_uint "APPFORGE_SMOKE_TIMEOUT" "$APPFORGE_SMOKE_TIMEOUT"
validate_uint "APPFORGE_BRIDGE_TIMEOUT" "$APPFORGE_BRIDGE_TIMEOUT"

validate_nonnegative_uint() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    die "$name must be a non-negative integer; got '$value'."
  fi
}

validate_nonnegative_uint "APPFORGE_WEB_PORT_FALLBACK_LIMIT" "$APPFORGE_WEB_PORT_FALLBACK_LIMIT"

cleanup() {
  local status=$?
  set +e
  if [[ -n "${WEB_PID:-}" ]] && kill -0 "$WEB_PID" >/dev/null 2>&1; then
    log "Stopping web server pid $WEB_PID."
    kill "$WEB_PID" >/dev/null 2>&1
    wait "$WEB_PID" >/dev/null 2>&1
  fi
  if [[ -n "${BRIDGE_PID:-}" ]] && kill -0 "$BRIDGE_PID" >/dev/null 2>&1; then
    log "Stopping llm_bridge pid $BRIDGE_PID."
    kill "$BRIDGE_PID" >/dev/null 2>&1
    wait "$BRIDGE_PID" >/dev/null 2>&1
  fi
  exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

require_curl() {
  have curl || die "curl is required for smoke and bridge health checks."
}

http_ok() {
  local url="$1"
  curl -fsS --max-time 2 --output /dev/null "$url" >/dev/null 2>&1
}

port_available() {
  local port="$1"
  if have lsof; then
    ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi
  "$ROOT_DIR/.venv/bin/python" -c 'import socket, sys; host=sys.argv[1]; port=int(sys.argv[2]); family=socket.AF_INET6 if ":" in host else socket.AF_INET; sock=socket.socket(family, socket.SOCK_STREAM); sock.bind((host, port)); sock.close()' "$APPFORGE_WEB_HOST" "$port"
}

bridge_reserved_web_port() {
  "$ROOT_DIR/.venv/bin/python" -c '
import sys
from urllib.parse import urlparse

parsed = urlparse(sys.argv[1])
web_host = sys.argv[2]
loopbacks = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}

try:
    port = parsed.port
except ValueError:
    port = None

if port is None and parsed.scheme in {"http", "https"}:
    port = 443 if parsed.scheme == "https" else 80

host = parsed.hostname or ""
if port and (host == web_host or (host in loopbacks and web_host in loopbacks)):
    print(port)
' "$APPFORGE_LLM_BRIDGE_URL" "$APPFORGE_WEB_HOST"
}

select_web_port() {
  local requested_port=$((10#$APPFORGE_WEB_PORT))
  local fallback_limit=$((10#$APPFORGE_WEB_PORT_FALLBACK_LIMIT))
  local end_port=$((requested_port + fallback_limit))
  local bridge_port
  local port
  local requested_reason=""

  if (( end_port > 65535 )); then
    end_port=65535
  fi

  bridge_port="$(bridge_reserved_web_port)"

  for (( port = requested_port; port <= end_port; port++ )); do
    if [[ -n "$bridge_port" && "$port" == "$bridge_port" ]]; then
      if (( port == requested_port )); then
        requested_reason="reserved for llm_bridge"
      fi
      continue
    fi
    if port_available "$port"; then
      if (( port != requested_port )); then
        if [[ -z "$requested_reason" ]]; then
          requested_reason="already in use"
        fi
        log "Web port $requested_port is $requested_reason; using $port instead."
      fi
      APPFORGE_WEB_PORT="$port"
      WEB_URL="http://${APPFORGE_WEB_HOST}:${APPFORGE_WEB_PORT}"
      return 0
    elif (( port == requested_port )); then
      requested_reason="already in use"
    fi
  done

  die "No available web port found from $requested_port through $end_port. Set APPFORGE_WEB_PORT to a free port or increase APPFORGE_WEB_PORT_FALLBACK_LIMIT."
}

tail_web_log() {
  if [[ -f "$WEB_LOG" ]]; then
    log "Last web server log lines:"
    tail -n 40 "$WEB_LOG" >&2
  fi
}

install_python_with_pip() {
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    have python3 || die "python3 is required when uv sync is unavailable or unusable."
    log "Creating .venv with python3."
    python3 -m venv "$ROOT_DIR/.venv" || die "python3 -m venv .venv failed."
  fi

  if ! "$ROOT_DIR/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
    log "Bootstrapping pip in .venv."
    "$ROOT_DIR/.venv/bin/python" -m ensurepip --upgrade || die "pip is missing and python -m ensurepip failed."
  fi

  local pip_args=(-e '.[dev]')
  if "$ROOT_DIR/.venv/bin/python" -c 'import setuptools, wheel' >/dev/null 2>&1; then
    pip_args=(--no-build-isolation -e '.[dev]')
    log "Installing Python dependencies with pip editable fallback without build isolation."
  else
    log "Installing Python dependencies with pip editable fallback."
  fi
  "$ROOT_DIR/.venv/bin/python" -m pip install "${pip_args[@]}" || die "pip install ${pip_args[*]} failed."
}

uv_supports_sync() {
  uv sync --help >/dev/null 2>&1
}

ensure_python_env() {
  if is_true "${APPFORGE_SKIP_INSTALL:-}"; then
    log "Skipping Python dependency sync because APPFORGE_SKIP_INSTALL is set."
  elif have uv && uv_supports_sync; then
    log "Syncing Python dependencies with uv."
    if ! uv sync --extra dev; then
      log "uv sync failed; falling back to pip editable install."
      install_python_with_pip
    fi
  elif have uv; then
    log "Installed uv does not support 'uv sync'; falling back to pip editable install."
    install_python_with_pip
  else
    log "uv is unavailable; falling back to pip editable install."
    install_python_with_pip
  fi

  if [[ ! -x "$APPFORGE_BIN" ]]; then
    die ".venv/bin/appforge is missing or not executable. Re-run without APPFORGE_SKIP_INSTALL or install the project with .venv/bin/python -m pip install -e '.[dev]'."
  fi
}

ensure_frontend() {
  if is_true "${APPFORGE_SKIP_FRONTEND_BUILD:-}"; then
    log "Skipping frontend build because APPFORGE_SKIP_FRONTEND_BUILD is set."
    [[ -f "$ROOT_DIR/appforge/resources/web/index.html" ]] || die "Packaged web assets are missing; run npm --prefix frontend run build."
    return
  fi

  have npm || die "npm is required to install/build the frontend."
  if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    log "Installing frontend dependencies."
    npm --prefix frontend install || die "npm --prefix frontend install failed."
  fi

  log "Building packaged frontend assets."
  npm --prefix frontend run build || die "npm --prefix frontend run build failed."
  [[ -f "$ROOT_DIR/appforge/resources/web/index.html" ]] || die "Frontend build did not produce appforge/resources/web/index.html."
}

normalize_driver() {
  printf '%s' "${1:-auto}" | tr '[:upper:]_' '[:lower:]-'
}

maybe_start_bridge() {
  local driver
  driver="$(normalize_driver "${APPFORGE_DRIVER:-llm-bridge-agent}")"
  local requested=0
  if [[ "$driver" == "llm-bridge" || "$driver" == "llm-bridge-agent" || "$driver" == "auto" ]] || is_true "${APPFORGE_START_LLM_BRIDGE:-}"; then
    requested=1
  fi

  if (( requested == 0 )); then
    return
  fi

  if is_true "${APPFORGE_SKIP_LLM_BRIDGE:-}"; then
    log "Skipping llm_bridge startup because APPFORGE_SKIP_LLM_BRIDGE is set."
    return
  fi
  # The FastAPI process owns bridge startup so its high-entropy capability stays
  # in memory and the child receives an allowlisted environment. Starting Bun
  # here would either expose the capability in argv or forward the whole shell
  # environment to a credential-bearing process.
  log "Secure llm_bridge startup will be handled by the AppForge web process."
}

build_web_command() {
  WEB_CMD=("$APPFORGE_BIN" web --host "$APPFORGE_WEB_HOST" --port "$APPFORGE_WEB_PORT" --log-level "$APPFORGE_LOG_LEVEL")
  if (( NO_OPEN == 1 )); then
    WEB_CMD+=(--no-open-browser)
  fi
}

start_web_background() {
  mkdir -p "$APPFORGE_RUNTIME_PATH"
  log "Starting AppForge web smoke server at $WEB_URL."
  build_web_command
  "${WEB_CMD[@]}" >"$WEB_LOG" 2>&1 &
  WEB_PID=$!
}

wait_for_web_endpoint() {
  local label="$1"
  local url="$2"
  local deadline=$((SECONDS + APPFORGE_SMOKE_TIMEOUT))

  while (( SECONDS < deadline )); do
    if [[ -n "${WEB_PID:-}" ]] && ! kill -0 "$WEB_PID" >/dev/null 2>&1; then
      log "Web server exited before $label became ready."
      tail_web_log
      return 1
    fi
    if http_ok "$url"; then
      log "$label is ready: $url"
      return 0
    fi
    sleep 1
  done

  log "Timed out after ${APPFORGE_SMOKE_TIMEOUT}s waiting for $label at $url."
  tail_web_log
  return 1
}

run_smoke() {
  require_curl
  start_web_background
  wait_for_web_endpoint "Health endpoint" "$WEB_URL/api/health" || exit 1
  wait_for_web_endpoint "Web UI" "$WEB_URL/" || exit 1
  log "Smoke check passed for $WEB_URL."
}

run_check() {
  log "Check passed. AppForge web assets and launcher dependencies are ready."
}

run_foreground_web() {
  build_web_command
  if (( NO_OPEN == 1 )); then
    log "Launching AppForge web UI at $WEB_URL without opening a browser."
  else
    log "Launching AppForge web UI at $WEB_URL and opening the default browser."
  fi
  "${WEB_CMD[@]}"
}

ensure_python_env
ensure_frontend
select_web_port
maybe_start_bridge

case "$MODE" in
  smoke)
    run_smoke
    ;;
  check)
    run_check
    ;;
  serve)
    run_foreground_web
    ;;
  *)
    die "Unknown launcher mode: $MODE"
    ;;
esac
