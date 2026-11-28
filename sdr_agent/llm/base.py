"""모델에 상관없이 쓰는 LLM 인터페이스.

에이전트 루프는 이 파일의 타입만 안다. Claude와 Gemini의 메시지 형식 차이는
각 어댑터가 변환한다. 덕분에 같은 시나리오를 두 모델로 돌려 비교할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    """LLM에게 보여 줄 도구 설명. input_schema는 JSON Schema."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: Any
    is_error: bool = False


@dataclass
class Message:
    """대화 기록의 한 항목.

    role:
      - "user": text만 사용
      - "assistant": text와 tool_calls. raw에는 모델이 돌려준 원본을 둔다
        (Gemini는 함수 호출 원본을 그대로 돌려줘야 하는 경우가 있다)
      - "tool": tool_results만 사용
    """

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    raw: Any = None
    provider: str | None = None


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    raw: Any = None
    truncated: bool = False  # 최대 출력 길이에서 잘림 (도구 입력이 불완전할 수 있음)


class LLM(Protocol):
    provider: str
    model: str

    def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int = 2048,
    ) -> LLMResponse: ...
