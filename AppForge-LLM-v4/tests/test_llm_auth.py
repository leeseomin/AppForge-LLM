from __future__ import annotations

from appforge import llm_auth


class FakePrompt:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def ask(self) -> str | None:
        return self._value


def test_state_label():
    assert llm_auth._state_label({"has_key": True, "key_source": "env"}) == "환경변수"
    assert llm_auth._state_label({"has_key": True, "key_source": "stored"}) == "키 저장됨"
    assert llm_auth._state_label({"has_key": False}) == "키 없음"


def test_parse_choice():
    assert llm_auth._parse_choice("deepseek  ·  DeepSeek  ·  키 저장됨") == "deepseek"
    assert llm_auth._parse_choice(None) is None
    assert llm_auth._parse_choice("") is None


def test_model_choices():
    choices = llm_auth._model_choices([{"id": "m1", "name": "M1"}, {"id": "m2"}])
    assert choices == ["m1  ·  M1", "m2  ·  m2"]


def test_cmd_list_prints_stored_credentials(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "list_providers",
        lambda *a, **k: {
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "has_key": True,
                    "key_source": "stored",
                    "default_model": "deepseek-v4-pro",
                }
            ]
        },
    )
    monkeypatch.setattr(
        llm_auth.llm_bridge, "get_active", lambda *a, **k: {"provider": "deepseek", "model": "deepseek-v4-pro"}
    )
    assert llm_auth.cmd_list("http://127.0.0.1:8788") == 0


def test_cmd_list_no_credentials(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(llm_auth.llm_bridge, "list_providers", lambda *a, **k: {"providers": []})
    monkeypatch.setattr(llm_auth.llm_bridge, "get_active", lambda *a, **k: {"provider": None, "model": None})
    assert llm_auth.cmd_list("http://127.0.0.1:8788") == 0


def test_cmd_login_non_interactive(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "list_providers",
        lambda *a, **k: {
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "has_key": False,
                    "key_source": "none",
                    "default_model": None,
                    "base_url": "https://api.deepseek.com",
                    "base_url_required": None,
                }
            ]
        },
    )
    monkeypatch.setattr(llm_auth.llm_bridge, "get_active", lambda *a, **k: {"provider": None, "model": None})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "provider_models",
        lambda *a, **k: {"models": [{"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"}]},
    )
    calls: dict = {}

    def fake_upsert(url, pid, **body):
        calls["upsert"] = (pid, body)
        return {}

    monkeypatch.setattr(llm_auth.llm_bridge, "upsert_provider", fake_upsert)
    monkeypatch.setattr(llm_auth.llm_bridge, "test_provider", lambda *a, **k: {"ok": True, "text": "ok"})

    def fake_set_active(url, p, m, **k):
        calls["active"] = (p, m)
        return {}

    monkeypatch.setattr(llm_auth.llm_bridge, "set_active", fake_set_active)
    monkeypatch.setattr(llm_auth.questionary, "password", lambda *a, **k: FakePrompt("sk-test"))
    monkeypatch.setattr(
        llm_auth.questionary, "autocomplete", lambda *a, **k: FakePrompt("deepseek-v4-pro  ·  DeepSeek V4 Pro")
    )
    rc = llm_auth.cmd_login("http://127.0.0.1:8788", provider_id="deepseek")
    assert rc == 0
    assert calls["upsert"][0] == "deepseek"
    assert calls["upsert"][1]["api_key"] == "sk-test"
    assert calls["active"] == ("deepseek", "deepseek-v4-pro")


def test_cmd_login_test_failure_keeps_credential(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "list_providers",
        lambda *a, **k: {
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "has_key": False,
                    "key_source": "none",
                    "default_model": None,
                    "base_url": "https://api.deepseek.com",
                    "base_url_required": None,
                }
            ]
        },
    )
    monkeypatch.setattr(llm_auth.llm_bridge, "get_active", lambda *a, **k: {"provider": None, "model": None})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "provider_models",
        lambda *a, **k: {"models": [{"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"}]},
    )
    activated: list = []
    monkeypatch.setattr(llm_auth.llm_bridge, "upsert_provider", lambda *a, **k: {})
    monkeypatch.setattr(llm_auth.llm_bridge, "test_provider", lambda *a, **k: {"ok": False, "error": "bad key"})
    monkeypatch.setattr(
        llm_auth.llm_bridge, "set_active", lambda *a, **k: activated.append(1) or {}
    )
    monkeypatch.setattr(llm_auth.questionary, "password", lambda *a, **k: FakePrompt("sk-bad"))
    monkeypatch.setattr(
        llm_auth.questionary, "autocomplete", lambda *a, **k: FakePrompt("deepseek-v4-pro  ·  DeepSeek V4 Pro")
    )
    rc = llm_auth.cmd_login("http://127.0.0.1:8788", provider_id="deepseek")
    assert rc == 1
    assert activated == []


def test_cmd_logout(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "list_providers",
        lambda *a, **k: {
            "providers": [
                {"id": "deepseek", "name": "DeepSeek", "has_key": True, "key_source": "stored"}
            ]
        },
    )
    monkeypatch.setattr(llm_auth.llm_bridge, "get_active", lambda *a, **k: {"provider": "deepseek", "model": "x"})
    monkeypatch.setattr(llm_auth.llm_bridge, "delete_provider", lambda *a, **k: {"ok": True})
    cleared: list = []
    monkeypatch.setattr(llm_auth.llm_bridge, "set_active", lambda *a, **k: cleared.append(1) or {})
    assert llm_auth.cmd_logout("http://127.0.0.1:8788", provider_id="deepseek") == 0
    assert cleared == [1]


def test_cmd_use(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "list_providers",
        lambda *a, **k: {
            "providers": [
                {"id": "deepseek", "name": "DeepSeek", "has_key": True, "key_source": "stored", "default_model": "deepseek-v4-pro"}
            ]
        },
    )
    monkeypatch.setattr(llm_auth.llm_bridge, "get_active", lambda *a, **k: {"provider": "deepseek", "model": "old"})
    activated: list = []
    monkeypatch.setattr(
        llm_auth.llm_bridge, "set_active", lambda url, p, m, **k: activated.append((p, m)) or {}
    )
    assert llm_auth.cmd_use("http://127.0.0.1:8788", provider_id="deepseek", model_id="deepseek-v4-pro") == 0
    assert activated == [("deepseek", "deepseek-v4-pro")]


def test_cmd_models_for_provider(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "provider_models",
        lambda *a, **k: {"name": "DeepSeek", "models": [{"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"}]},
    )
    assert llm_auth.cmd_models("http://127.0.0.1:8788", provider_id="deepseek") == 0


def test_cmd_models_refresh(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(llm_auth.llm_bridge, "refresh_catalog", lambda *a, **k: {"catalog_loaded": True})
    monkeypatch.setattr(llm_auth.llm_bridge, "list_providers", lambda *a, **k: {"providers": []})
    monkeypatch.setattr(llm_auth.llm_bridge, "get_active", lambda *a, **k: {"provider": None, "model": None})
    assert llm_auth.cmd_models("http://127.0.0.1:8788", refresh=True) == 0


def test_cmd_login_oauth_success(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "oauth_providers",
        lambda *a, **k: {
            "providers": [
                {
                    "id": "openai",
                    "name": "openai",
                    "methods": [{"id": "browser", "label": "ChatGPT"}],
                }
            ]
        },
    )
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "oauth_start",
        lambda *a, **k: {"pollId": "test-poll-id", "url": "https://auth.openai.com/...", "instructions": "Complete auth", "method": "browser"},
    )
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "oauth_poll",
        lambda *a, **k: {"status": "success", "provider": "openai", "credential": {"type": "oauth", "refresh": "r", "access": "a", "expires": 0}},
    )
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "provider_models",
        lambda *a, **k: {"models": [{"id": "gpt-5.2", "name": "GPT-5.2"}]},
    )
    activated: list = []
    monkeypatch.setattr(llm_auth.llm_bridge, "set_active", lambda url, p, m, **k: activated.append((p, m)) or {})
    monkeypatch.setattr(llm_auth.webbrowser, "open", lambda *a, **k: True)
    monkeypatch.setattr(llm_auth.questionary, "autocomplete", lambda *a, **k: FakePrompt("gpt-5.2  ·  GPT-5.2"))
    assert llm_auth.cmd_login_oauth("http://127.0.0.1:8788", provider_id="openai") == 0
    assert activated == [("openai", "gpt-5.2")]


def test_cmd_login_oauth_failed(monkeypatch):
    monkeypatch.setattr(llm_auth.llm_bridge, "ping", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "oauth_providers",
        lambda *a, **k: {
            "providers": [
                {
                    "id": "xai",
                    "name": "xai",
                    "methods": [{"id": "device-code", "label": "xAI Headless"}],
                }
            ]
        },
    )
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "oauth_start",
        lambda *a, **k: {"pollId": "test-poll-id", "url": "https://auth.x.ai/...", "instructions": "Enter code", "method": "device-code"},
    )
    monkeypatch.setattr(
        llm_auth.llm_bridge,
        "oauth_poll",
        lambda *a, **k: {"status": "failed", "error": "authorization denied"},
    )
    assert llm_auth.cmd_login_oauth("http://127.0.0.1:8788", provider_id="xai") == 1
