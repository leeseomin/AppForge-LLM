from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from appforge.models import ToolResult
from appforge.util import truncate

from ..base import Tool


class HttpProbeTool(Tool):
    name = "http_probe"
    description = "Probe a local or explicitly allowed HTTP endpoint for smoke testing."
    capability = "verification"
    network_required = False

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        url = str(inputs.get("url", "http://127.0.0.1:3000/"))
        parsed = urllib.parse.urlparse(url)
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if parsed.hostname not in local_hosts and not bool(inputs.get("allow_network", False)):
            return ToolResult(success=False, error="Non-local HTTP probes require allow_network=true")
        method = str(inputs.get("method", "GET")).upper()
        body = inputs.get("json")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"User-Agent": "OpenAppForge/0.1", **{str(k): str(v) for k, v in (inputs.get("headers") or {}).items()}}
        if data is not None:
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=float(inputs.get("timeout", 10))) as response:
                raw = response.read(int(inputs.get("max_bytes", 100_000)))
                text = raw.decode("utf-8", errors="replace")
                status = response.status
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read(100_000)
            text = raw.decode("utf-8", errors="replace")
            status = exc.code
            response_headers = dict(exc.headers.items()) if exc.headers else {}
        except Exception as exc:
            return ToolResult(success=False, error=f"HTTP probe failed: {exc}")
        expected = inputs.get("expected_status", [200, 201, 202, 204])
        expected_set = {int(expected)} if isinstance(expected, int) else {int(x) for x in expected}
        return ToolResult(
            success=status in expected_set,
            error=None if status in expected_set else f"Unexpected HTTP status {status}",
            data={
                "url": url,
                "status": status,
                "headers": response_headers,
                "body": truncate(text, 20_000),
                "duration_seconds": round(time.monotonic() - started, 4),
            },
        )
