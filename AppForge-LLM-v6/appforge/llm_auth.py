"""Interactive LLM provider connection flows for the AppForge CLI.

Mirrors the coco ``coco auth login`` experience: one command, an autocomplete
provider pick, a single API-key prompt, an optional model pick, then save +
test + activate — all through the local ``llm_bridge`` HTTP API so the bridge
stays the single source of truth for provider metadata (models.dev catalog).

The bridge is auto-started via :class:`appforge.llm_bridge_process.LLMBridgeProcessManager`
so these commands work even when the web UI is not running.
"""

from __future__ import annotations

import time
import webbrowser
from typing import Any

try:
    import questionary  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal test envs
    class _MissingQuestionaryPrompt:
        def ask(self) -> None:
            return None

    class _MissingQuestionary:
        @staticmethod
        def autocomplete(*_args: Any, **_kwargs: Any) -> _MissingQuestionaryPrompt:
            return _MissingQuestionaryPrompt()

        @staticmethod
        def password(*_args: Any, **_kwargs: Any) -> _MissingQuestionaryPrompt:
            return _MissingQuestionaryPrompt()

        @staticmethod
        def text(*_args: Any, **_kwargs: Any) -> _MissingQuestionaryPrompt:
            return _MissingQuestionaryPrompt()

    questionary = _MissingQuestionary()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import llm_bridge
from .drivers import DEFAULT_LLM_BRIDGE_URL
from .llm_bridge_process import LLMBridgeProcessManager

console = Console()


class AuthError(RuntimeError):
    """Raised when an auth flow cannot complete."""


def _ensure_bridge(bridge_url: str, manager: LLMBridgeProcessManager | None = None) -> None:
    own = manager is None
    mgr = manager or LLMBridgeProcessManager()
    try:
        llm_bridge.ping(bridge_url, timeout=2.0)
        return
    except llm_bridge.BridgeError:
        pass
    try:
        mgr.ensure_running(bridge_url)
    except llm_bridge.BridgeError as exc:
        raise AuthError(str(exc)) from exc
    finally:
        if own:
            mgr.shutdown()


def _state_label(status: dict[str, Any]) -> str:
    if status.get("has_key"):
        src = status.get("key_source")
        if src == "env":
            return "환경변수"
        if src == "stored":
            return "키 저장됨"
        return "키 있음"
    return "키 없음"


def _provider_choices(statuses: list[dict[str, Any]]) -> list[str]:
    return [
        f"{s['id']}  ·  {s['name']}  ·  {_state_label(s)}"
        + ("  ·  활성" if s.get("id") == _active_id_holder.get(s["id"]) else "")
        for s in statuses
    ]


_active_id_holder: dict[str, str] = {}


def _parse_choice(choice: str | None) -> str | None:
    if not choice:
        return None
    return choice.split("  ·")[0].strip()


def _model_choices(models: list[dict[str, Any]]) -> list[str]:
    return [f"{m['id']}  ·  {m.get('name') or m['id']}" for m in models]


def _pick_provider(statuses: list[dict[str, Any]], *, hint: str | None = None) -> dict[str, Any] | None:
    if not statuses:
        console.print("[red]사용 가능한 프로바이더가 없습니다.[/red]")
        return None
    choices = _provider_choices(statuses)
    selected = questionary.autocomplete(
        "프로바이더 선택",
        choices=choices,
    ).ask()
    pid = _parse_choice(selected)
    if not pid:
        return None
    return next((s for s in statuses if s["id"] == pid), None)


def cmd_login(
    bridge_url: str = DEFAULT_LLM_BRIDGE_URL,
    *,
    provider_id: str | None = None,
    manager: LLMBridgeProcessManager | None = None,
) -> int:
    """Interactive: pick provider → enter API key → save → test → activate model."""
    _ensure_bridge(bridge_url, manager)
    try:
        providers_payload = llm_bridge.list_providers(bridge_url)
        active = llm_bridge.get_active(bridge_url)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]프로바이더 목록을 불러오지 못했습니다:[/red] {exc}")
        return 1

    _active_id_holder.clear()
    if active.get("provider"):
        _active_id_holder[active["provider"]] = active["provider"]

    statuses: list[dict[str, Any]] = providers_payload.get("providers", [])

    chosen: dict[str, Any] | None = None
    if provider_id:
        chosen = next((s for s in statuses if s["id"] == provider_id), None)
        if not chosen:
            console.print(f"[red]알 수 없는 프로바이더:[/red] {provider_id}")
            return 1
    else:
        chosen = _pick_provider(statuses)
    if not chosen:
        console.print("[yellow]취소되었습니다.[/yellow]")
        return 1

    pid: str = chosen["id"]
    name: str = chosen["name"]

    if active.get("provider") == pid:
        console.print(f"현재 활성 프로바이더: [bold]{name}[/bold] / {active.get('model') or '기본 모델'}")

    key_prompt = "API 키 입력"
    if chosen.get("has_key"):
        if chosen.get("key_source") == "env":
            key_prompt = f"API 키 (환경변수 {chosen.get('env_key')} 사용 중, 덮어쓰려면 입력)"
        else:
            key_prompt = "API 키 (저장된 키 사용 중, 덮어쓰려면 입력)"
    api_key = questionary.password(key_prompt).ask()
    if api_key is None:
        console.print("[yellow]취소되었습니다.[/yellow]")
        return 1
    api_key = api_key.strip()

    base_url_override: str | None = None
    if chosen.get("base_url_required") and not chosen.get("base_url"):
        base_url_override = questionary.text("Base URL (필수)").ask()
        if not base_url_override:
            console.print("[red]이 프로바이더는 Base URL이 필요합니다.[/red]")
            return 1

    try:
        models_payload = llm_bridge.provider_models(bridge_url, pid)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]모델 목록을 불러오지 못했습니다:[/red] {exc}")
        return 1
    models: list[dict[str, Any]] = models_payload.get("models", [])
    if not models:
        console.print("[red]이 프로바이더는 카탈로그에 모델이 없습니다. 모델 ID를 수동으로 입력해야 합니다.[/red]")
        return 1

    default_model = chosen.get("default_model") or active.get("model")
    model_choices = _model_choices(models)
    selected_model = questionary.autocomplete(
        "사용할 모델",
        choices=model_choices,
        default=default_model or "",
    ).ask()
    model_id = _parse_choice(selected_model) or default_model or models[0]["id"]

    body: dict[str, Any] = {}
    if api_key:
        body["api_key"] = api_key
    if base_url_override:
        body["base_url_override"] = base_url_override
    if model_id:
        body["default_model"] = model_id

    try:
        llm_bridge.upsert_provider(bridge_url, pid, **body)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]저장에 실패했습니다:[/red] {exc}")
        return 1
    console.print(f"[green]✓[/green] {name} 자격증명 저장됨")

    console.print(f"[dim]연결 테스트 중... ({model_id})[/dim]")
    try:
        result = llm_bridge.test_provider(
            bridge_url,
            pid,
            api_key=api_key or None,
            base_url_override=base_url_override,
            model=model_id,
            timeout=30.0,
        )
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]연결 테스트 실패:[/red] {exc}")
        return 1

    if not result.get("ok"):
        console.print(f"[red]✕ 연결 실패:[/red] {result.get('error') or '알 수 없는 오류'}")
        console.print("자격증명은 저장되었지만 활성화하지 못했습니다. 키와 Base URL을 확인하세요.")
        return 1

    reply = (result.get("text") or "").strip()
    console.print("[green]✓ 연결 성공[/green]" + (f" — 응답: “{reply}”" if reply else ""))

    try:
        llm_bridge.set_active(bridge_url, pid, model_id)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]활성 모델 설정 실패:[/red] {exc}")
        return 1

    console.print(
        Panel.fit(
            f"활성 프로바이더: [bold]{name}[/bold]\n모델: [bold]{model_id}[/bold]\n이제 `appforge forge \"...\"` 로 파이프라인을 실행할 수 있습니다.",
            title="AppForge · LLM 연결 완료",
        )
    )
    return 0


def cmd_list(
    bridge_url: str = DEFAULT_LLM_BRIDGE_URL,
    *,
    manager: LLMBridgeProcessManager | None = None,
) -> int:
    _ensure_bridge(bridge_url, manager)
    try:
        providers_payload = llm_bridge.list_providers(bridge_url)
        active = llm_bridge.get_active(bridge_url)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]불러오기 실패:[/red] {exc}")
        return 1

    statuses = [s for s in providers_payload.get("providers", []) if s.get("has_key")]
    table = Table(title="저장된 자격증명")
    table.add_column("Provider", style="bold")
    table.add_column("이름")
    table.add_column("키 출처")
    table.add_column("기본 모델")
    table.add_column("활성", overflow="fold")
    for s in statuses:
        is_active = s["id"] == active.get("provider")
        table.add_row(
            s["id"],
            s["name"],
            s.get("key_source", "-"),
            s.get("default_model") or "-",
            f"[green]{active.get('model') or '기본'}[/green]" if is_active else "-",
        )
    console.print(table)
    if not statuses:
        console.print("[dim]저장된 자격증명이 없습니다. `appforge auth login` 으로 추가하세요.[/dim]")
    return 0


def cmd_logout(
    bridge_url: str = DEFAULT_LLM_BRIDGE_URL,
    *,
    provider_id: str | None = None,
    manager: LLMBridgeProcessManager | None = None,
) -> int:
    _ensure_bridge(bridge_url, manager)
    try:
        providers_payload = llm_bridge.list_providers(bridge_url)
        active = llm_bridge.get_active(bridge_url)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]불러오기 실패:[/red] {exc}")
        return 1

    stored = [s for s in providers_payload.get("providers", []) if s.get("has_key") and s.get("key_source") == "stored"]
    if not stored:
        console.print("[dim]저장된 키가 없습니다.[/dim]")
        return 0

    if not provider_id:
        choice = questionary.autocomplete(
            "로그아웃할 프로바이더",
            choices=[f"{s['id']}  ·  {s['name']}" for s in stored],
        ).ask()
        provider_id = _parse_choice(choice)
    if not provider_id:
        console.print("[yellow]취소되었습니다.[/yellow]")
        return 0

    try:
        llm_bridge.delete_provider(bridge_url, provider_id)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]삭제 실패:[/red] {exc}")
        return 1
    if active.get("provider") == provider_id:
        try:
            llm_bridge.set_active(bridge_url, None, None)
        except llm_bridge.BridgeError:
            pass
    console.print(f"[green]✓[/green] {provider_id} 자격증명 삭제됨")
    return 0


def cmd_use(
    bridge_url: str = DEFAULT_LLM_BRIDGE_URL,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
    manager: LLMBridgeProcessManager | None = None,
) -> int:
    _ensure_bridge(bridge_url, manager)
    try:
        providers_payload = llm_bridge.list_providers(bridge_url)
        active = llm_bridge.get_active(bridge_url)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]불러오기 실패:[/red] {exc}")
        return 1

    statuses = providers_payload.get("providers", [])
    configured = [s for s in statuses if s.get("has_key")]
    if not configured:
        console.print("[red]구성된 프로바이더가 없습니다. 먼저 `appforge auth login` 을 실행하세요.[/red]")
        return 1

    if not provider_id:
        default_choice = active.get("provider") or ""
        choice = questionary.autocomplete(
            "활성 프로바이더",
            choices=[f"{s['id']}  ·  {s['name']}" for s in configured],
            default=default_choice,
        ).ask()
        provider_id = _parse_choice(choice)
    if not provider_id:
        console.print("[yellow]취소되었습니다.[/yellow]")
        return 0

    chosen = next((s for s in statuses if s["id"] == provider_id), None)
    if not chosen:
        console.print(f"[red]알 수 없는 프로바이더:[/red] {provider_id}")
        return 1
    if not chosen.get("has_key"):
        console.print(f"[red]{provider_id} 에 API 키가 없습니다. `appforge auth login` 먼저.[/red]")
        return 1

    if not model_id:
        try:
            models_payload = llm_bridge.provider_models(bridge_url, provider_id)
        except llm_bridge.BridgeError as exc:
            console.print(f"[red]모델 목록 실패:[/red] {exc}")
            return 1
        models = models_payload.get("models", [])
        default_model = chosen.get("default_model") or active.get("model") or ""
        choice = questionary.autocomplete(
            "모델",
            choices=_model_choices(models),
            default=default_model,
        ).ask()
        model_id = _parse_choice(choice)
    if not model_id:
        console.print("[yellow]취소되었습니다.[/yellow]")
        return 0

    try:
        llm_bridge.set_active(bridge_url, provider_id, model_id)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]활성 모델 설정 실패:[/red] {exc}")
        return 1
    console.print(
        Panel.fit(
            f"활성 프로바이더: [bold]{chosen['name']}[/bold]\n모델: [bold]{model_id}[/bold]",
            title="AppForge · 활성 모델 변경",
        )
    )
    return 0


def cmd_models(
    bridge_url: str = DEFAULT_LLM_BRIDGE_URL,
    *,
    provider_id: str | None = None,
    refresh: bool = False,
    manager: LLMBridgeProcessManager | None = None,
) -> int:
    _ensure_bridge(bridge_url, manager)
    if refresh:
        try:
            data = llm_bridge.refresh_catalog(bridge_url)
            console.print(f"[green]카탈로그 새로고침:[/green] {'성공' if data.get('catalog_loaded') else '실패(오프라인 fallback)'}")
        except llm_bridge.BridgeError as exc:
            console.print(f"[red]카탈로그 새로고침 실패:[/red] {exc}")
            return 1

    try:
        if provider_id:
            payload = llm_bridge.provider_models(bridge_url, provider_id)
            table = Table(title=f"{payload.get('name') or provider_id} 모델")
            table.add_column("Model ID", style="bold")
            table.add_column("이름")
            for m in payload.get("models", []):
                table.add_row(m["id"], m.get("name") or m["id"])
            console.print(table)
        else:
            payload = llm_bridge.list_providers(bridge_url)
            table = Table(title="프로바이더별 모델 수")
            table.add_column("Provider", style="bold")
            table.add_column("이름")
            table.add_column("모델 수")
            table.add_column("활성")
            active = llm_bridge.get_active(bridge_url)
            for s in payload.get("providers", []):
                is_active = s["id"] == active.get("provider")
                table.add_row(
                    s["id"],
                    s["name"],
                    str(s.get("model_count", len(s.get("models", [])))),
                    "[green]✓[/green]" if is_active else "-",
                )
            console.print(table)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]불러오기 실패:[/red] {exc}")
        return 1
    return 0


def cmd_login_oauth(
    bridge_url: str = DEFAULT_LLM_BRIDGE_URL,
    *,
    provider_id: str | None = None,
    manager: LLMBridgeProcessManager | None = None,
) -> int:
    """Interactive OAuth login: pick provider → method → browser/device → poll → activate."""
    _ensure_bridge(bridge_url, manager)
    try:
        oauth_payload = llm_bridge.oauth_providers(bridge_url)
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]OAuth 프로바이더 목록을 불러오지 못했습니다:[/red] {exc}")
        return 1

    providers = oauth_payload.get("providers", [])
    if not providers:
        console.print("[red]OAuth를 지원하는 프로바이더가 없습니다.[/red]")
        return 1

    if not provider_id:
        choices = [f"{p['id']}  ·  {p.get('name', p['id'])}" for p in providers]
        selected = questionary.autocomplete("OAuth 프로바이더 선택", choices=choices).ask()
        provider_id = _parse_choice(selected)
    if not provider_id:
        console.print("[yellow]취소되었습니다.[/yellow]")
        return 0

    provider_desc = next((p for p in providers if p["id"] == provider_id), None)
    if not provider_desc:
        console.print(f"[red]알 수 없는 OAuth 프로바이더:[/red] {provider_id}")
        return 1

    methods = provider_desc.get("methods", [])
    if not methods:
        console.print(f"[red]{provider_id} 에 사용 가능한 OAuth 방식이 없습니다.[/red]")
        return 1

    method_id = methods[0]["id"] if len(methods) == 1 else None
    if not method_id:
        selected = questionary.select(
            "로그인 방식 선택",
            choices=[f"{m['id']}  ·  {m['label']}" for m in methods],
        ).ask()
        method_id = _parse_choice(selected) if selected else None
    if not method_id:
        console.print("[yellow]취소되었습니다.[/yellow]")
        return 0

    enterprise_domain: str | None = None
    if provider_id == "github-copilot":
        is_enterprise = questionary.confirm("GitHub Enterprise를 사용하시나요?", default=False).ask()
        if is_enterprise:
            enterprise_domain = questionary.text("Enterprise 도메인 (예: company.ghe.com)").ask()
            if not enterprise_domain:
                console.print("[red]Enterprise 도메인이 필요합니다.[/red]")
                return 1

    console.print(f"[dim]OAuth 플로우 시작 중... ({provider_id} / {method_id})[/dim]")
    try:
        start_result = llm_bridge.oauth_start(
            bridge_url,
            provider=provider_id,
            method=method_id,
            enterprise_domain=enterprise_domain,
        )
    except llm_bridge.BridgeError as exc:
        console.print(f"[red]OAuth 시작 실패:[/red] {exc}")
        return 1

    poll_id = start_result.get("pollId", "")
    auth_url = start_result.get("url", "")
    instructions = start_result.get("instructions", "")

    console.print(Panel.fit(
        f"[bold]{provider_id}[/bold] · {method_id}\n\n{instructions}\n\nURL: {auth_url}",
        title="AppForge · OAuth 로그인",
    ))

    if method_id == "browser" and auth_url:
        try:
            webbrowser.open(auth_url)
            console.print("[dim]브라우저를 열었습니다. 완료 후 이 창으로 돌아오세요.[/dim]")
        except Exception:
            console.print(f"[yellow]브라우저를 자동으로 열 수 없습니다. 수동으로 URL을 여세요:[/yellow] {auth_url}")

    console.print("[dim]인증 완료를 기다리는 중... (Ctrl+C로 취소)[/dim]")
    deadline = time.monotonic() + 5 * 60
    while time.monotonic() < deadline:
        try:
            poll_result = llm_bridge.oauth_poll(bridge_url, provider_id, poll_id)
        except llm_bridge.BridgeError as exc:
            console.print(f"[red]폴링 실패:[/red] {exc}")
            return 1
        status = poll_result.get("status")
        if status == "success":
            credential = poll_result.get("credential", {})
            console.print(f"[green]✓ OAuth 인증 성공[/green] ({provider_id})")
            if credential.get("accountId"):
                console.print(f"[dim]Account ID: {credential['accountId']}[/dim]")

            try:
                models_payload = llm_bridge.provider_models(bridge_url, provider_id)
            except llm_bridge.BridgeError:
                models_payload = {"models": []}
            models = models_payload.get("models", [])
            if models:
                default_model = models[0]["id"]
                selected_model = questionary.autocomplete(
                    "사용할 모델",
                    choices=_model_choices(models),
                    default=default_model,
                ).ask()
                model_id = _parse_choice(selected_model) or default_model
            else:
                model_id = None

            try:
                llm_bridge.set_active(bridge_url, provider_id, model_id)
            except llm_bridge.BridgeError as exc:
                console.print(f"[red]활성 모델 설정 실패:[/red] {exc}")
                return 1
            console.print(
                Panel.fit(
                    f"활성 프로바이더: [bold]{provider_id}[/bold]\n모델: [bold]{model_id or '기본'}[/bold]",
                    title="AppForge · OAuth 로그인 완료",
                )
            )
            return 0
        if status == "failed":
            error = poll_result.get("error", "알 수 없는 오류")
            console.print(f"[red]✕ OAuth 인증 실패:[/red] {error}")
            return 1
        time.sleep(2)

    console.print("[red]OAuth 인증 시간 초과 (5분)[/red]")
    return 1
