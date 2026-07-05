from __future__ import annotations

import threading

from appforge import llm_bridge


class _FakeSocket:
    def __init__(self, connection: "_BlockingConnection") -> None:
        self.connection = connection

    def shutdown(self, _how: int) -> None:
        self.connection.shutdown_called.set()
        self.connection.close()


class _BlockingConnection:
    def __init__(self) -> None:
        self.request_seen = threading.Event()
        self.response_waiting = threading.Event()
        self.closed = threading.Event()
        self.shutdown_called = threading.Event()
        self.sock = _FakeSocket(self)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        assert method == "POST"
        assert path == "/generate"
        assert body is not None
        assert headers["Content-Type"] == "application/json"
        self.request_seen.set()

    def getresponse(self) -> object:
        self.response_waiting.set()
        self.closed.wait(timeout=5)
        raise OSError("connection closed")

    def close(self) -> None:
        self.closed.set()


def test_generate_cancels_in_flight_bridge_http_request(monkeypatch) -> None:
    connection = _BlockingConnection()
    monkeypatch.setattr(
        llm_bridge.http.client,
        "HTTPConnection",
        lambda *args, **kwargs: connection,
    )
    cancel_event = threading.Event()
    errors: list[BaseException] = []

    def call_bridge() -> None:
        try:
            llm_bridge.generate(
                "http://bridge.test",
                prompt="Generate a tiny app",
                timeout=30,
                cancel_event=cancel_event,
            )
        except BaseException as exc:  # pragma: no cover - asserted from caller thread
            errors.append(exc)

    worker = threading.Thread(target=call_bridge)
    worker.start()
    assert connection.request_seen.wait(timeout=2)
    assert connection.response_waiting.wait(timeout=2)

    cancel_event.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert connection.shutdown_called.is_set()
    assert len(errors) == 1
    assert isinstance(errors[0], llm_bridge.BridgeCancelled)
