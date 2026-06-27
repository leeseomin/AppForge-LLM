from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?([^\s'\"]{8,})"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str, *, fallback: str = "app") -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    chars: list[str] = []
    dash = False
    for ch in normalized:
        if ch.isalnum():
            chars.append(ch)
            dash = False
        elif not dash:
            chars.append("-")
            dash = True
    slug = "".join(chars).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        slug = fallback
    return slug[:72].rstrip("-") or fallback


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def safe_resolve(root: Path, candidate: str | Path) -> Path:
    root = root.resolve()
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path escapes workspace: {candidate}")
    return resolved


def redact(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(lambda m: f"{m.group(1)}=<redacted>", redacted)
        else:
            redacted = pattern.sub("<redacted-secret>", redacted)
    return redacted


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = max(1, (limit - 80) // 2)
    return f"{text[:half]}\n... <truncated {len(text) - (2 * half)} chars> ...\n{text[-half:]}"


def command_exists(command: str) -> bool:
    from shutil import which

    return which(command) is not None


def python_module_exists(module: str) -> bool:
    import importlib.util

    spec = importlib.util.find_spec(module)
    # A same-named local directory can appear as a namespace package even when
    # no importable tool is installed (for example a repository's ./build/).
    return spec is not None and spec.origin is not None


def iter_files(
    root: Path,
    *,
    ignored_dirs: Iterable[str] = (),
    max_files: int | None = None,
) -> Iterable[Path]:
    ignored = set(ignored_dirs)
    count = 0
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ignored and not d.endswith(".egg-info"))
        for filename in sorted(filenames):
            if filename == ".DS_Store":
                continue
            path = Path(current) / filename
            if path.is_symlink():
                continue
            yield path
            count += 1
            if max_files is not None and count >= max_files:
                return
