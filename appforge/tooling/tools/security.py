from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from appforge.constants import IGNORED_DIRS
from appforge.models import ToolResult
from appforge.util import atomic_write_json, command_exists, iter_files, safe_resolve

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
_CONFIG_SECRET_SUFFIXES = {
    ".cfg",
    ".conf",
    ".ini",
    ".json",
    ".properties",
    ".toml",
    ".yaml",
    ".yml",
}
_IDENTIFIER_EXPRESSION = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
_SECRET_RULES = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    ("anthropic_api_key", re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{20,})\b")),
    ("openrouter_api_key", re.compile(r"\b(sk-or-v1-[A-Za-z0-9_-]{20,})\b")),
    ("openai_api_key", re.compile(r"\b(sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,})\b")),
    ("google_api_key", re.compile(r"\b(AIza[0-9A-Za-z_-]{30,})\b")),
    ("xai_api_key", re.compile(r"\b(xai-[A-Za-z0-9_-]{20,})\b", re.I)),
    ("jwt", re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b")),
    ("npm_auth_token", re.compile(r"(?i)(?:_authToken|_auth)\s*=\s*([^\s#]{8,})")),
    (
        "generic_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|credential)\b"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9_./+=:@-]{8,})"
        ),
    ),
]


def _finding(rule: str, path: str, line: int | None) -> dict[str, Any]:
    return {
        "rule": rule,
        "path": path,
        "line": line,
        # Never include source excerpts here. Redaction regexes are necessarily
        # incomplete, while a rule/path/line tuple is sufficient to remediate.
        "preview": "[REDACTED]",
    }


def _looks_like_generic_secret(
    candidate: str,
    relative_path: str,
    line: str,
    match: re.Match[str],
) -> bool:
    normalized = candidate.strip("'\" ")
    lowered = normalized.casefold()
    if lowered.startswith(("process.env", "os.environ")):
        return False

    name = Path(relative_path).name.casefold()
    config_like = (
        name.startswith(".env")
        or name in {".netrc", ".npmrc", ".pypirc"}
        or Path(name).suffix in _CONFIG_SECRET_SUFFIXES
    )
    fragment = line[match.start():match.end()]
    quoted = bool(re.search(r"[:=]\s*['\"]", fragment))
    if not quoted and not config_like and _IDENTIFIER_EXPRESSION.fullmatch(normalized):
        return False
    if config_like:
        return True

    classes = sum(
        (
            any(char.islower() for char in normalized),
            any(char.isupper() for char in normalized),
            any(char.isdigit() for char in normalized),
            any(not char.isalnum() for char in normalized),
        )
    )
    return len(normalized) >= 20 or (len(normalized) >= 12 and classes >= 3)


def scan_file_bytes(
    raw: bytes,
    relative_path: str,
    *,
    max_file_bytes: int = 2_000_000,
) -> list[dict[str, Any]]:
    if len(raw) > max_file_bytes:
        return [_finding("unscannable_large_file", relative_path, None)]
    text = raw.decode("utf-8", errors="ignore")
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule_name, pattern in _SECRET_RULES:
            match = pattern.search(line)
            if not match:
                continue
            candidate = match.group(1) if match.lastindex else match.group(0)
            normalized = candidate.strip("'\" ").casefold()
            if (
                normalized in _PLACEHOLDERS
                or "${" in candidate
            ):
                continue
            if rule_name == "generic_secret" and not _looks_like_generic_secret(
                candidate,
                relative_path,
                line,
                match,
            ):
                continue
            identity = (rule_name, line_number)
            if identity in seen:
                continue
            seen.add(identity)
            findings.append(_finding(rule_name, relative_path, line_number))
    return findings


def scan_paths_for_secrets(
    workspace: Path,
    paths: list[Path],
    *,
    max_file_bytes: int = 2_000_000,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in paths:
        rel = path.relative_to(workspace).as_posix()
        try:
            raw = path.read_bytes()
        except OSError:
            findings.append(_finding("unreadable_file", rel, None))
            continue
        findings.extend(
            scan_file_bytes(raw, rel, max_file_bytes=max_file_bytes)
        )
    return findings


class SecretScanTool(Tool):
    name = "secret_scan"
    description = "Scan source text for likely committed credentials and private keys."
    capability = "security"
    llm_exposed = True
    llm_description = "Scan source text for likely committed credentials and private keys."

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        max_file_bytes = int(inputs.get("max_file_bytes", 2_000_000))
        paths = [
            path
            for path in iter_files(workspace, ignored_dirs=IGNORED_DIRS, max_files=50_000)
            if not path.relative_to(workspace).as_posix().startswith(".appforge/")
        ]
        findings = scan_paths_for_secrets(
            workspace,
            paths,
            max_file_bytes=max_file_bytes,
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
