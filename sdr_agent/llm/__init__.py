"""LLM 어댑터. get_llm("claude" | "gemini")으로 만든다."""

from __future__ import annotations

from ..config import Settings, load_settings
from .base import LLM, LLMResponse, Message, ToolCall, ToolResult, ToolSpec

__all__ = ["LLM", "LLMResponse", "Message", "ToolCall", "ToolResult", "ToolSpec", "get_llm"]


def get_llm(provider: str | None = None, settings: Settings | None = None) -> LLM:
    s = settings or load_settings()
    provider = provider or s.default_llm
    if provider == "claude":
        if not s.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다")
        from .anthropic_llm import ClaudeLLM

        return ClaudeLLM(s.anthropic_api_key, s.anthropic_model)
    if provider == "gemini":
        if not s.gemini_api_key or not s.gemini_model:
            raise RuntimeError("GEMINI_API_KEY와 GEMINI_MODEL이 필요합니다")
        from .gemini_llm import GeminiLLM

        return GeminiLLM(s.gemini_api_key, s.gemini_model)
    raise ValueError(f"알 수 없는 LLM: {provider}")
