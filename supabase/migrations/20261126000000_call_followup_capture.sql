-- 전화 팔로업에서 들은 내용을 따로 남긴다 (인계 요약과 평가에 쓴다)
-- 전화는 사람이 직접 걸고(전화 시스템 연동 없음), 결과도 사람이 기록한다.
-- 콜 과제를 시스템이 자동 취소하는 경우는 수신 거부 하나뿐이다 (DB 트리거, 20261125010000_call_tasks.sql).

alter table public.call_tasks
  add column email_received boolean,       -- 메일을 받았다고 했는지
  add column meeting_preference text,      -- 희망 미팅 시기 (고객이 말한 그대로)
  add column compelling_reason text;       -- 왜 지금 바꿔야 하는지, 들은 내용
