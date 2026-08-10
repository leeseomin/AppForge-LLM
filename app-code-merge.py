#!/usr/bin/env python3
"""Merge a source tree into one reviewable Markdown file.

This is the cross-platform implementation used by the POSIX and PowerShell
wrappers. It intentionally excludes dependencies, generated output, binary
assets, common secret files, and symbolic links.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

EXCLUDED_DIRECTORIES = {
    ".agents",
    ".cache",
    ".codex",
    ".git",
    ".hg",
    ".idea",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".svn",
    ".turbo",
    ".venv",
    ".vite",
    ".vscode",
    "__pycache__",
    "bower_components",
    "build",
    "coverage",
    "dist",
    "env",
    "node_modules",
    "out",
    "target",
    "vendor",
    "venv",
}

EXCLUDED_SUFFIXES = {
    ".7z",
    ".avi",
    ".avif",
    ".bz2",
    ".cache",
    ".crt",
    ".cube",
    ".db",
    ".dll",
    ".dylib",
    ".eot",
    ".exe",
    ".flac",
    ".gif",
    ".gz",
    ".icns",
    ".ico",
    ".jpeg",
    ".jpg",
    ".key",
    ".log",
    ".map",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".otf",
    ".p12",
    ".pdf",
    ".pem",
    ".png",
    ".pyc",
    ".pyo",
    ".rar",
    ".so",
    ".sqlite",
    ".sqlite3",
    ".svg",
    ".tar",
    ".temp",
    ".tgz",
    ".tmp",
    ".tsbuildinfo",
    ".ttf",
    ".wasm",
    ".wav",
    ".webp",
    ".woff",
    ".woff2",
    ".xz",
    ".zip",
}

SPECIAL_SOURCE_NAMES = {
    ".dockerignore",
    ".editorconfig",
    ".env.example",
    ".gitignore",
    ".npmrc",
    "Brewfile",
    "Cargo.lock",
    "Cargo.toml",
    "Containerfile",
    "Dockerfile",
    "Gemfile",
    "Jenkinsfile",
    "Makefile",
    "Pipfile",
    "Pipfile.lock",
    "Procfile",
    "Rakefile",
    "bun.lock",
    "bun.lockb",
    "composer.json",
    "composer.lock",
    "constraints.txt",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}

SOURCE_SUFFIXES = {
    ".bash",
    ".c",
    ".cc",
    ".cfg",
    ".cjs",
    ".clj",
    ".cljs",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".dart",
    ".edn",
    ".erl",
    ".ex",
    ".exs",
    ".fish",
    ".go",
    ".gql",
    ".gradle",
    ".graphql",
    ".h",
    ".hpp",
    ".hrl",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonc",
    ".jsx",
    ".kt",
    ".kts",
    ".less",
    ".lua",
    ".mjs",
    ".md",
    ".mdx",
    ".php",
    ".py",
    ".pyi",
    ".r",
    ".rb",
    ".rs",
    ".sass",
    ".scala",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".yaml",
    ".yml",
    ".zsh",
}

LANGUAGES = {
    ".bash": "bash",
    ".c": "cpp",
    ".cc": "cpp",
    ".cjs": "javascript",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".dart": "dart",
    ".go": "go",
    ".gql": "graphql",
    ".gradle": "java",
    ".graphql": "graphql",
    ".h": "cpp",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsonc": "json",
    ".jsx": "javascript",
    ".kt": "java",
    ".kts": "java",
    ".lua": "lua",
    ".md": "markdown",
    ".mdx": "markdown",
    ".mjs": "javascript",
    ".php": "php",
    ".py": "python",
    ".pyi": "python",
    ".r": "r",
    ".rb": "ruby",
    ".rs": "rust",
    ".sass": "scss",
    ".scss": "scss",
    ".sh": "bash",
    ".sql": "sql",
    ".svelte": "svelte",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zsh": "bash",
}

HELPER_NAMES = {"app-code-merge.py", "app-code-merge.ps1", "app-code-merge.sh"}
SECRET_NAMES = {".env", "id_ed25519", "id_rsa"}


def _max_file_bytes() -> int:
    raw = os.environ.get("APP_CODE_MERGE_MAX_FILE_BYTES", "1048576")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("APP_CODE_MERGE_MAX_FILE_BYTES must be a positive integer") from exc
    if value <= 0:
        raise ValueError("APP_CODE_MERGE_MAX_FILE_BYTES must be greater than zero")
    return value


def _is_secret_name(name: str) -> bool:
    lower = name.casefold()
    return lower in SECRET_NAMES or lower.startswith(".env.") and lower != ".env.example"


def _is_named_source(name: str) -> bool:
    if name in SPECIAL_SOURCE_NAMES:
        return True
    lower = name.casefold()
    return (
        lower.startswith("requirements-") and lower.endswith(".txt")
        or lower.startswith("constraints-") and lower.endswith(".txt")
        or lower.startswith("tsconfig.") and lower.endswith(".json")
        or lower.startswith((
            "vite.config.",
            "next.config.",
            "nuxt.config.",
            "svelte.config.",
            "tailwind.config.",
            "postcss.config.",
            "eslint.config.",
            "prettier.config.",
        ))
    )


def _should_include(path: Path, *, output_name: str, max_file_bytes: int) -> bool:
    name = path.name
    lower_name = name.casefold()
    if name in HELPER_NAMES or name == output_name or _is_secret_name(name):
        return False
    if path.suffix.casefold() in EXCLUDED_SUFFIXES:
        return False
    try:
        if path.stat().st_size > max_file_bytes:
            return False
        with path.open("rb") as handle:
            if b"\x00" in handle.read(8192):
                return False
    except OSError:
        return False
    return _is_named_source(name) or path.suffix.casefold() in SOURCE_SUFFIXES


def _language(path: Path) -> str:
    if path.name in {"Dockerfile", "Containerfile"}:
        return "dockerfile"
    if path.name == "Makefile":
        return "makefile"
    return LANGUAGES.get(path.suffix.casefold(), "")


def _source_files(root: Path, *, output_name: str, max_file_bytes: int) -> list[Path]:
    selected: list[Path] = []
    for current_root, directories, files in os.walk(root, followlinks=False):
        current = Path(current_root)
        directories[:] = [
            name
            for name in directories
            if name not in EXCLUDED_DIRECTORIES and not (current / name).is_symlink()
        ]
        for name in files:
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue
            if _should_include(path, output_name=output_name, max_file_bytes=max_file_bytes):
                selected.append(path.relative_to(root))
    return sorted(selected, key=lambda path: path.as_posix())


def _write_merged(root: Path, output: Path, files: list[Path]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(f"# {root.name} Source Code\n\n")
            handle.write(f"Source root: `{root}`\n\n")
            handle.write("Generated by: `app-code-merge.py`\n\n")
            handle.write(f"Files merged: {len(files)}\n\n")
            for relative in files:
                source = root / relative
                handle.write(f"## File: {relative.as_posix()}\n\n")
                handle.write(f"````{_language(relative)}\n")
                handle.write(source.read_text(encoding="utf-8", errors="replace"))
                handle.write("\n````\n\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def merge(app_dir: Path) -> Path:
    root = app_dir.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"app directory does not exist: {root}")
    app_name = root.name or root.drive.replace(":", "") or "app"
    output = root / f"{app_name}-source-code.md"
    max_file_bytes = _max_file_bytes()
    files = _source_files(root, output_name=output.name, max_file_bytes=max_file_bytes)
    if not files:
        raise RuntimeError(f"no source files found in '{root}'")

    print(f"Merging source code for: {app_name}")
    print(f"Source root: {root}")
    print(f"Output file: {output.name}")
    print(f"Files merged: {len(files)}")
    print("Excluding dependency, cache, build output, archive, binary, and secret files")
    _write_merged(root, output, files)
    print(f"Created: {output.name} ({output.stat().st_size} bytes)")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_dir", nargs="?", default=".", help="source project directory")
    args = parser.parse_args(argv)
    try:
        merge(Path(args.app_dir))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
