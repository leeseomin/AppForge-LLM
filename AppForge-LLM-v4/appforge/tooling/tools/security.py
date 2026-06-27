from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from appforge.constants import IGNORED_DIRS
from appforge.models import ToolResult
from appforge.util import atomic_write_json, command_exists, iter_files, redact, safe_resolve, truncate

from ..base import Tool
from ..command import CommandPolicy, run_command
from ..detection import detect_stack

_PLACEHOLDERS = {
    "changeme",
    "change-me",
    "example",
    "placeholder",
    "your-api-key",
    "test",
    "dummy",
    "not-a-secret",
    "<secret>",
    "<token>",
}
_SECRET_RULES = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    ("generic_secret", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]([^'\"\s]{8,})['\"]")),
]


class SecretScanTool(Tool):
    name = "secret_scan"
    description = "Scan source text for likely committed credentials and private keys."
    capability = "security"

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        findings: list[dict[str, Any]] = []
        max_file_bytes = int(inputs.get("max_file_bytes", 2_000_000))
        for path in iter_files(workspace, ignored_dirs=IGNORED_DIRS, max_files=50_000):
            rel = path.relative_to(workspace).as_posix()
            if rel.startswith(".appforge/"):
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if len(raw) > max_file_bytes or b"\x00" in raw[:8192]:
                continue
            text = raw.decode("utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), start=1):
                for rule_name, pattern in _SECRET_RULES:
                    match = pattern.search(line)
                    if not match:
                        continue
                    candidate = match.group(1) if match.lastindex else match.group(0)
                    normalized = candidate.strip("'\" ").casefold()
                    if normalized in _PLACEHOLDERS or "${" in candidate or "process.env" in line or "os.environ" in line:
                        continue
                    findings.append(
                        {
                            "rule": rule_name,
                            "path": rel,
                            "line": line_number,
                            "preview": redact(truncate(line, 180)),
                        }
                    )
        return ToolResult(
            success=not findings,
            error=None if not findings else f"Found {len(findings)} possible secret(s)",
            data={"passed": not findings, "findings": findings},
        )


class DependencyAuditTool(Tool):
    name = "dependency_audit"
    description = "Run the ecosystem vulnerability audit command when available."
    capability = "security"
    network_required = True

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        stack = detect_stack(workspace)
        managers = stack.get("package_managers") or []
        command: list[str] | None = None
        if "npm" in managers and command_exists("npm"):
            command = ["npm", "audit", "--omit=dev", "--audit-level=high"]
        elif "pnpm" in managers and command_exists("pnpm"):
            command = ["pnpm", "audit", "--prod", "--audit-level", "high"]
        elif "yarn" in managers and command_exists("yarn"):
            command = ["yarn", "npm", "audit", "--severity", "high"]
        elif "python" in stack.get("languages", []) and command_exists("pip-audit"):
            command = ["pip-audit"]
        elif "cargo" in managers and command_exists("cargo-audit"):
            command = ["cargo", "audit"]
        elif "go" in managers and command_exists("govulncheck"):
            command = ["govulncheck", "./..."]
        if command is None:
            return ToolResult(
                success=True,
                data={"skipped": True, "reason": "No supported dependency auditor is installed", "stack": stack},
            )
        result = run_command(
            workspace,
            command,
            policy=CommandPolicy(
                allow_network=bool(inputs.get("allow_network", False)),
                timeout_seconds=int(inputs.get("timeout", 600)),
            ),
        )
        result.data["skipped"] = False
        return result


class LicenseInventoryTool(Tool):
    name = "license_inventory"
    description = "Create a manifest-level dependency inventory for license review."
    capability = "compliance"

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        components: list[dict[str, Any]] = []
        package_json = workspace / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8"))
                for scope in ("dependencies", "devDependencies", "peerDependencies"):
                    for name, version in sorted((data.get(scope) or {}).items()):
                        components.append({"ecosystem": "npm", "name": name, "version": str(version), "scope": scope, "license": "unknown"})
            except (OSError, json.JSONDecodeError):
                pass
        requirements = workspace / "requirements.txt"
        if requirements.exists():
            for raw in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
                components.append({"ecosystem": "pypi", "name": name, "version": line[len(name):].strip() or "unspecified", "scope": "runtime", "license": "unknown"})
        cargo = workspace / "Cargo.toml"
        if cargo.exists():
            section = ""
            for raw in cargo.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if line.startswith("["):
                    section = line.strip("[]")
                elif section in {"dependencies", "dev-dependencies", "build-dependencies"} and "=" in line and not line.startswith("#"):
                    name, version = [part.strip() for part in line.split("=", 1)]
                    components.append({"ecosystem": "cargo", "name": name, "version": version.strip('"'), "scope": section, "license": "unknown"})
        output = safe_resolve(workspace, str(inputs.get("output", ".appforge/reports/license-inventory.json")))
        payload = {"version": "1.0", "component_count": len(components), "components": components}
        atomic_write_json(output, payload)
        return ToolResult(success=True, data=payload, artifacts=[str(output)])


class GenerateSbomTool(Tool):
    name = "generate_sbom"
    description = "Generate a lightweight CycloneDX-shaped SBOM from project manifests."
    capability = "compliance"

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        inventory = LicenseInventoryTool().execute(workspace, {"output": ".appforge/reports/license-inventory.json"})
        components = [
            {
                "type": "library",
                "name": item["name"],
                "version": item["version"],
                "purl": f"pkg:{item['ecosystem']}/{item['name']}@{item['version']}",
                "properties": [{"name": "openappforge:scope", "value": item["scope"]}],
            }
            for item in inventory.data.get("components", [])
        ]
        payload = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {"component": {"type": "application", "name": workspace.name}},
            "components": components,
        }
        output = safe_resolve(workspace, str(inputs.get("output", ".appforge/reports/sbom.cdx.json")))
        atomic_write_json(output, payload)
        return ToolResult(success=True, data={"components": len(components), "output": output.relative_to(workspace).as_posix()}, artifacts=[str(output)])
