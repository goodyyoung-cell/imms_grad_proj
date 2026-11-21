"""에이전트가 쓸 도구 정의와 레지스트리."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..llm.base import ToolSpec


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Any]
    # True면 이 도구를 부르는 순간 루프가 끝난다 (예: 결과 제출)
    terminal: bool = False

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.input_schema)


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.add(t)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"도구 이름 중복: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)
