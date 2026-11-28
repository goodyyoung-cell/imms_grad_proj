# 도메인 특화 AI SDR 에이전트

KAIST 정보경영프로그램 현장연구 프로젝트 (2026 가을학기)

**SaaS·AI 솔루션 영업의 확장성과 표준화를 위한 도메인 특화 AI SDR 에이전트 설계 및 프로토타입 개발**

## 배경

SaaS·AI 솔루션 시장이 대기업에서 중소기업으로 넓어지고 있지만, 고객 발굴과 수요 창출을 맡는 SDR(Sales Development Representative) 업무는 여전히 인력 의존적이다. 담당 인원과 비용만큼만 고객을 만날 수 있고, 담당자 역량에 따라 접촉 빈도, 메시지 적합성, 후속 대응의 품질이 달라진다.

이 프로젝트는 영업 실무 경험을 바탕으로 영업 지식과 고객 선별 기준을 반영한 AI SDR 에이전트를 만들고, AI와 인간 SDR이 역할을 나누는 협업 구조를 제안한다.

## 구성

1. **고객 리서치·선별**: 기업·산업 정보와 솔루션 적합성 파악, 접촉 우선순위 제안
2. **이메일 소통**: 고객별 메시지 생성, 발송 일정, 답장 분류, 후속 응대
3. **초기 스크리닝 콜**: 미팅 예약 연결, AI가 도입 수요 확인
4. **인간 SDR 인계**: 리서치·접촉 이력·선별 근거·권장 조치 요약
5. **모의 콜·코칭**: 고객 상황 기반 역할극과 피드백

## 문서

- [프로젝트 계획서 요약](docs/project-plan.md)
- [주요 결정 기록](docs/decisions.md)
- [이번 학기 구현 범위](docs/scope.md)
- [Claude 작업 맥락 (CLAUDE.md)](CLAUDE.md)

## 팀

- 한민수
- 손영훈

## 시작하기

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # 값을 채워 넣기. .env는 커밋되지 않습니다
pytest                 # API 키 없이 도는 테스트

python -m sdr_agent.cli research --limit 3   # 리서치 에이전트 실행
```

Supabase와 Cloud Run 설정은 [배포 가이드](docs/deploy.md)를 참고합니다.
