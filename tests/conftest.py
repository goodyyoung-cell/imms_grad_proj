"""모든 테스트 공통: .env에 실제 키가 있어도 테스트가 외부 LLM·API를 부르지 않게 비운다."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DART_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
                 "BOOKING_URL"):
        monkeypatch.setenv(name, "")
