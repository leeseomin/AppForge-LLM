"""Host-computed source identity shared by repair, validation, and export."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from .constants import IGNORED_DIRS


class SnapshotError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def source_manifest(root: Path) -> dict[str, dict[str, Any]]:
    """Include untracked files, deletions and executable bits; never follow links."""
    root = root.resolve()
    manifest: dict[str, dict[str, Any]] = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in IGNORED_DIRS
                                 and not name.endswith(".egg-info"))
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise SnapshotError(f"Source directory symlink is not supported: {name}")
        for name in sorted(files):
            path = Path(directory) / name
            if name == ".DS_Store" or name.endswith((".pyc", ".pyo")):
                continue
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise SnapshotError(f"Source must be a regular file: {relative}")
            if len(manifest) >= 100_000 or info.st_size > 20_000_000:
                raise SnapshotError(f"Source snapshot limit exceeded: {relative}")
            raw = path.read_bytes()
            manifest[relative] = {"sha256": hashlib.sha256(raw).hexdigest(),
                                  "size": len(raw), "executable": bool(info.st_mode & 0o111)}
    return manifest


def changed_paths(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def exportable(relative: str) -> bool:
    path = Path(relative)
    name = path.name.casefold()
    sensitive = {".env", "id_rsa", "id_ed25519", "credentials", "credentials.json",
                 "service-account.json", ".npmrc", ".pypirc", ".netrc", "auth.json"}
    return (name not in sensitive and not (name.startswith(".env.") and name != ".env.example")
            and path.suffix.casefold() not in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"})


def export_manifest(workspace: Path) -> dict[str, dict[str, Any]]:
    return {path: value for path, value in source_manifest(workspace).items() if exportable(path)}


def mutation_manifest(workspace: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = source_manifest(workspace)
    for path in sorted((workspace / ".appforge" / "artifacts").glob("*.json")):
        if path.is_symlink():
            raise SnapshotError("Artifact symlink is not supported")
        manifest[path.relative_to(workspace).as_posix()] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return manifest
