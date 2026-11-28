# 샘플 데이터

## leads.json: 평가용 리드 106건 (대표 100 + 중복 6)

`scripts/build_sample_leads.py`로 만든다 (2026-11-25 시가총액 기준).

- **회사는 실제 상장사**: KOSPI 시가총액 상위 100개사 중 50개사, KOSDAQ 상위 100개사 중 50개사.
  - 보통주만 센다 (우선주·ETF·스팩 제외). 순위 출처: 네이버 증권. 업종(WICS)·홈페이지 출처: WiseReport 기업개요.
  - 업종이 고르게 섞이도록 업종을 돌아가며 시가총액이 큰 회사부터 하나씩 뽑았다. KOSPI 40개 업종, KOSDAQ 28개 업종, 한 업종 최대 3개사.
  - 원자료와 선정 여부: [`market_cap_top100.csv`](market_cap_top100.csv)
- **담당자·연락처·직급·문의 메모는 모두 가상**이다 (고정 시드 무작위 생성). 메모의 문의 내용은 해당 회사의 실제 상황이 아니다.
  - 이메일은 `@k<종목코드>.example` 예약 도메인이라 어디로도 발송될 수 없다. 전화는 `010-0000-xxxx`.
  - 실제 회사 도메인은 `company_domain`(리서치용 홈페이지)에만 쓴다.
- **중복 병합 검증용 6건** (`merged_into_key`): 같은 회사 다른 담당자 4건(D01~D04), 같은 사람이 이메일 대소문자·회사명 표기만 바꿔 다른 경로로 다시 들어온 2건(D05~D06).
- 유입 경로, 직급 표기(수평 호칭 포함), 경과일(0~14일)이 다양하다.

DB에 넣기: `python -m sdr_agent.cli seed`

## replies.json: 정답이 달린 모의 답장 24건

7개 분류(interested, question, not_now, not_decision_maker, out_of_office, unsubscribe, unclear)에 대한 정답 라벨.
근거 없는 질문(R06), 질문+미팅 의사(R21), '스팸'이 들어 있지만 수신 거부가 아닌 답장(R22) 같은 경계 사례를 포함한다.

정확도 측정: `python -m sdr_agent.cli eval-replies`

## leads_template.csv

직원이 시트로 리드를 올릴 때 쓰는 열 이름 예시. `python -m sdr_agent.cli import-leads --file ...`
