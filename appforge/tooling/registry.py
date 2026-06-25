from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections import defaultdict
from typing import Any

from .base import Tool


class ToolRegistry:
    _instance: "ToolRegistry | None" = None

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools = {}
            cls._instance._discovered = False
        return cls._instance

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            existing = type(self._tools[tool.name]).__name__
            raise ValueError(f"Duplicate tool {tool.name!r}: {existing}, {type(tool).__name__}")
        self._tools[tool.name] = tool

    def discover(self, *, force: bool = False) -> None:
        if self._discovered and not force:
            return
        if force:
            self._tools.clear()
        package = importlib.import_module("appforge.tooling.tools")
        for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            module = importlib.import_module(module_info.name)
            for _, cls in inspect.getmembers(module, inspect.isclass):
                if cls is Tool or not issubclass(cls, Tool) or inspect.isabstract(cls):
                    continue
                if cls.__name__.startswith("_") or "name" not in cls.__dict__:
                    continue
                if cls.__module__ != module.__name__:
                    continue
                self.register(cls())
        self._discovered = True

    def get(self, name: str) -> Tool:
        self.discover()
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool {name!r}. Available: {', '.join(self.names())}") from exc

    def names(self) -> list[str]:
        self.discover()
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        self.discover()
        return [self._tools[name] for name in self.names()]

    def by_capability(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for tool in self.all():
            grouped[tool.capability].append(tool.info())
        return dict(sorted(grouped.items()))

    def support_envelope(self) -> dict[str, dict[str, Any]]:
        return {tool.name: tool.info() for tool in self.all()}
