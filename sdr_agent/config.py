"""환경변수 설정. 값은 .env(로컬) 또는 Secret Manager(Cloud Run)에서 온다."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value else default


# 로컬 프론트엔드 개발 서버 (Next.js 3000, Vite 5173, 기타 흔한 포트)
DEFAULT_ORIGINS = ",".join(f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in (3000, 3001, 4173, 5173, 8080))


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    anthropic_model: str
    gemini_api_key: str | None
    gemini_model: str | None
    default_llm: str
    supabase_url: str | None
    supabase_secret_key: str | None
    dart_api_key: str | None
    job_token: str | None
    agent_max_steps: int
    booking_url: str | None = None      # 메일 CTA의 미팅 예약 링크 (모듈 3 축소)
    handoff_to: str | None = None       # 인계 자료를 받을 담당자 (이름 또는 주소)
    naver_client_id: str | None = None  # 네이버 검색 API (뉴스)
    naver_client_secret: str | None = None
    naver_search_api: str = "hub"       # hub: 네이버 클라우드 NAVER API HUB (기본), legacy: 예전 개발자센터 키
    google_news_enabled: bool = True    # Google 뉴스 RSS (키 불필요)
    admin_password: str | None = None  # 관리 화면 로그인 비밀번호 (팀 공유)
    admin_token_secret: str | None = None  # 로그인 토큰 서명 키 (없으면 JOB_TOKEN에서 만듦)
    allowed_origins: tuple[str, ...] = ()  # 관리 화면이 다른 주소에서 뜰 때 허용할 출처 (CORS)
    call_delay_minutes: int = 60        # 첫 메일 발송 후 최소 몇 분 뒤 전화할지
    call_hours_kst: tuple[int, int] = (9, 18)  # 전화해도 되는 시간대 (평일, 한국 시간, 시작 이상 끝 미만)


def load_settings() -> Settings:
    return Settings(
        anthropic_api_key=_get("ANTHROPIC_API_KEY"),
        anthropic_model=_get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        gemini_api_key=_get("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL"),
        default_llm=_get("DEFAULT_LLM", "claude"),
        supabase_url=_get("SUPABASE_URL"),
        supabase_secret_key=_get("SUPABASE_SECRET_KEY"),
        dart_api_key=_get("DART_API_KEY"),
        job_token=_get("JOB_TOKEN"),
        agent_max_steps=int(_get("AGENT_MAX_STEPS", "12")),
        naver_client_id=_get("NAVER_CLIENT_ID"),
        naver_client_secret=_get("NAVER_CLIENT_SECRET"),
        naver_search_api=_get("NAVER_SEARCH_API", "hub"),
        google_news_enabled=_get("GOOGLE_NEWS_ENABLED", "true").lower() not in ("0", "false", "no"),
        admin_password=_get("ADMIN_PASSWORD"),
        admin_token_secret=_get("ADMIN_TOKEN_SECRET"),
        allowed_origins=tuple(o.strip() for o in _get("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()),
        call_delay_minutes=int(_get("CALL_DELAY_MINUTES", "60")),
        call_hours_kst=tuple(int(h) for h in _get("CALL_HOURS_KST", "9-18").split("-", 1)),
        booking_url=_get("BOOKING_URL"),
        handoff_to=_get("HANDOFF_TO"),
    )
