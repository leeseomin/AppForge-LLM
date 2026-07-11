from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from appforge.constants import IGNORED_DIRS
from appforge.models import ToolResult
from appforge.util import atomic_write_text, iter_files, safe_resolve, truncate

from ..base import Tool


class WorkspaceTreeTool(Tool):
    name = "workspace_tree"
    description = "Return a bounded, ignore-aware tree of the project workspace."
    capability = "filesystem"
    llm_exposed = True
    llm_description = "Return a bounded project tree. Use this before reading or editing when you need orientation."
    input_schema = {
        "type": "object",
        "properties": {
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 12},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 5000},
        },
    }

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        max_depth = int(inputs.get("max_depth", 4))
        max_entries = int(inputs.get("max_entries", 500))
        entries: list[str] = []
        truncated_flag = False
        for current, dirs, files in os.walk(workspace):
            current_path = Path(current)
            depth = len(current_path.relative_to(workspace).parts)
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".git"))
            if depth >= max_depth:
                dirs[:] = []
            rel_dir = current_path.relative_to(workspace)
            if rel_dir != Path("."):
                entries.append(f"{rel_dir.as_posix()}/")
            for filename in sorted(files):
                rel = (rel_dir / filename).as_posix()
                entries.append(rel)
                if len(entries) >= max_entries:
                    truncated_flag = True
                    break
            if truncated_flag or len(entries) >= max_entries:
                break
        return ToolResult(success=True, data={"entries": entries, "truncated": truncated_flag})


class ReadTextTool(Tool):
    name = "read_text"
    description = "Read a UTF-8 text file from inside the workspace."
    capability = "filesystem"
    llm_exposed = True
    llm_description = "Read a UTF-8 text file in the workspace. Paths must be relative and cannot target managed control directories."
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
    }

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        path = safe_resolve(workspace, str(inputs["path"]))
        if not path.is_file():
            return ToolResult(success=False, error=f"Not a file: {path.relative_to(workspace)}")
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            return ToolResult(success=False, error="Binary files are not supported by read_text")
        text = raw.decode("utf-8", errors="replace")
        limit = max(100, int(inputs.get("max_chars", 40_000)))
        return ToolResult(
            success=True,
            data={"path": path.relative_to(workspace).as_posix(), "content": truncate(text, limit), "size": len(raw)},
        )


class WriteTextTool(Tool):
    name = "write_text"
    description = "Atomically write a UTF-8 text file inside the workspace."
    capability = "filesystem"
    llm_exposed = True
    llm_description = "Create or replace a UTF-8 text file in the workspace. Use small, targeted writes and preserve unrelated files."
    destructive = True
    input_schema = {
        "type": "object",
        "required": ["path", "content"],
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "overwrite": {"type": "boolean"}},
    }

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        path = safe_resolve(workspace, str(inputs["path"]))
        if path.exists() and not bool(inputs.get("overwrite", True)):
            return ToolResult(success=False, error=f"File exists and overwrite=false: {path.relative_to(workspace)}")
        atomic_write_text(path, str(inputs["content"]))
        return ToolResult(success=True, data={"path": path.relative_to(workspace).as_posix(), "chars": len(str(inputs["content"]))}, artifacts=[str(path)])


class SearchTextTool(Tool):
    name = "search_text"
    description = "Search text files in the workspace using a literal or regular expression."
    capability = "filesystem"
    llm_exposed = True
    llm_description = "Search workspace text files by literal or regex pattern to locate relevant code before editing."
    input_schema = {
        "type": "object",
        "required": ["pattern"],
        "properties": {
            "pattern": {"type": "string"},
            "glob": {"type": "string"},
            "regex": {"type": "boolean"},
            "case_sensitive": {"type": "boolean"},
            "max_hits": {"type": "integer"},
        },
    }

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        import re

        pattern = str(inputs["pattern"])
        use_regex = bool(inputs.get("regex", False))
        case_sensitive = bool(inputs.get("case_sensitive", False))
        max_hits = max(1, min(int(inputs.get("max_hits", 200)), 2000))
        glob_pattern = str(inputs.get("glob", "*"))
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = re.compile(pattern if use_regex else re.escape(pattern), flags)
        hits: list[dict[str, Any]] = []
        for path in iter_files(workspace, ignored_dirs=IGNORED_DIRS, max_files=20_000):
            rel = path.relative_to(workspace).as_posix()
            if not fnmatch.fnmatch(path.name, glob_pattern) and not fnmatch.fnmatch(rel, glob_pattern):
                continue
            try:
                raw = path.read_bytes()
                if b"\x00" in raw[:8192] or len(raw) > 4_000_000:
                    continue
                text = raw.decode("utf-8", errors="ignore")
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    hits.append({"path": rel, "line": line_number, "text": truncate(line, 500)})
                    if len(hits) >= max_hits:
                        return ToolResult(success=True, data={"hits": hits, "truncated": True})
        return ToolResult(success=True, data={"hits": hits, "truncated": False})
