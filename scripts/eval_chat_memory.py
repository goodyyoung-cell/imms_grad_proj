"""사용법: python scripts/eval_chat_memory.py <결과.json>  (gcloud 로그인과 Secret Manager 읽기 권한 필요)
점검: 소식 기반 초안(제목 후보·소재), 챗으로 다시 쓰기·피드백 저장, 저장한 피드백이 다음 초안에 반영되는지, 챗 발송 안전 규칙.
가짜 DB(메모리)와 실제 LLM을 쓴다. 실서버 DB에는 아무것도 쓰지 않고 Slack으로도 보내지 않는다. 사실(facts)은 배포 서버의 읽기 전용 API에서 가져온다.
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1])
GCLOUD = os.environ.get("GCLOUD", "gcloud")
API = "https://sdr-agent-840381264541.asia-northeast3.run.app"


def secret(name):
    return subprocess.run([GCLOUD, "secrets", "versions", "access", "latest", "--secret", name], capture_output=True,
                          text=True, shell=(os.name == "nt")).stdout.strip()


for k in ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL", "SLACK_APPROVERS", "ADMIN_WEB_URL", "SUPABASE_URL",
          "SUPABASE_SECRET_KEY", "PUBLIC_BASE_URL"):
    os.environ[k] = ""
os.environ["BOOKING_URL"] = "https://calendar.app.google/2i6Hupetf5cB4Zai6"
LLM_NAME = os.environ.get("EVAL_LLM", "claude")
if LLM_NAME == "gemini":
    os.environ["GEMINI_API_KEY"] = secret("gemini-api-key")
    os.environ["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    os.environ["GEMINI_THINKING_LEVEL"] = os.environ.get("GEMINI_THINKING_LEVEL", "low")
elif LLM_NAME == "openai":
    assert os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_MODEL"), "OPENAI_API_KEY와 OPENAI_MODEL을 환경변수로 주세요"
else:
    os.environ["ANTHROPIC_API_KEY"] = secret("anthropic-api-key")
    os.environ["ANTHROPIC_MODEL"] = "claude-sonnet-5-5"
os.environ["DEFAULT_LLM"] = LLM_NAME
pw = secret("admin-password")


def call(path, data=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(API + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
    return json.loads(urllib.request.urlopen(r, timeout=60).read().decode())


tok = call("/auth/login", {"name": "평가", "password": pw})["token"]
NAMES = ["성호전자", "씨젠", "하이브", "LG전자", "심텍"]
leads, facts = [], {}
for name in NAMES:
    lst = call(f"/admin/leads?q={urllib.request.quote(name)}&limit=5", tok=tok)["leads"]
    lead = next(l for l in lst if l["company_name"] == name)
    d = call(f"/admin/leads/{lead['id']}", tok=tok)
    leads.append({**d["lead"], "status": "researched", "unsubscribed_at": None})
    facts[lead["id"]] = d["facts"]

from sdr_agent import chat, jobs  # noqa: E402
from sdr_agent.agent import MemoryRunLog  # noqa: E402
from sdr_agent.config import load_settings  # noqa: E402
from sdr_agent.toolkit.news import GoogleNewsClient  # noqa: E402
from tests.test_chat_memory import MemRepo  # noqa: E402

settings = load_settings()
repo = MemRepo(leads)
repo.facts = facts
repo.scores = {l["id"]: {"score": 60, "tags": ["평가"], "breakdown": {}} for l in leads}
news = (None, GoogleNewsClient())
report = {"drafts": [], "chat": []}


class Log(MemoryRunLog):
    pass


logs = []


def new_log():
    log = Log()
    logs.append(log)
    return log


def view(mid):
    m = repo.messages[mid]
    lead = repo.leads[m["lead_id"]]
    body = m["body"].split("\n\n---\n")[0]
    return {"company": lead["company_name"], "subject": m["subject"], "options": m["subject_options"], "choice": m["subject_choice"],
            "hook": m["hook"], "body": body, "citations": m["citations"]}


for lead in leads:
    logs.clear()
    out = jobs.draft_emails([lead["id"]], settings=settings, repo=repo, new_log=new_log, news_clients=news)[0]
    errs = [s.output.get("error") for lg in logs for s in lg.steps if s.kind == "tool" and s.is_error]
    item = {"lead": lead["company_name"], "status": out["status"], "grounding_errors": errs}
    if out["status"] == "drafted":
        item.update(view(out["message_id"]), message_id=out["message_id"])
    report["drafts"].append(item)
    print(item["lead"], item["status"], "errors:", len(errs), "| hook:", (item.get("hook") or {}).get("type"), "|", item.get("subject"))

first = next(d for d in report["drafts"] if d["status"] == "drafted")
mid = first["message_id"]
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def talk(text, **ctx):
    logs.clear()
    out = chat.run_chat(text, user="평가", repo=repo, settings=settings, new_log=new_log, news_clients=news, context=ctx or None,
                        session_id=talk.sid)
    talk.sid = out["session_id"]
    report["chat"].append({"user": text, "reply": out["reply"], "actions": out["actions"]})
    print("\n>", text, "\n", out["reply"], "\n", [a["type"] for a in out["actions"]])
    return out


talk.sid = None
talk("대기 중인 승인 요청 보여줘")
talk("이 메일 제목을 더 짧게 다시 써줘", message_id=mid, lead_id=repo.messages[mid]["lead_id"], screen="message")
talk("앞으로 이 고객한테는 제목에 회사명을 넣지 말고 20자 안으로 써줘")
talk("이 메일 어때? 보내도 될 것 같아?")
report["after_rewrite"] = view(mid)
report["notes_after_chat"] = list(repo.notes.values())
status_before = repo.messages[mid]["status"]
talk("이 메일 발송해줘")
report["status_before_send_order"] = status_before
report["status_after_send_order"] = repo.messages[mid]["status"]
report["approvals"] = repo.approvals

# 피드백이 다음 초안에 반영되는지: 전체 범위 규칙을 저장하고, 다른 리드의 첫 메일 초안을 다시 만든다
rule = repo.insert_feedback({"scope": "global", "scope_key": None, "kind": "length", "text": "제목은 20자 안으로 쓴다",
                             "created_by": "평가", "source": "chat_web"})
second = next(d for d in report["drafts"] if d["lead"] == "씨젠" and d["status"] == "drafted")
old = repo.messages[second["message_id"]]
old["status"] = "rejected"
repo.leads[old["lead_id"]]["status"] = "researched"
logs.clear()
again = jobs.draft_emails([old["lead_id"]], settings=settings, repo=repo, new_log=new_log, news_clients=news)[0]
loop = {"before": second["options"], "rule_id": rule["id"], "status": again["status"]}
if again["status"] == "drafted":
    v = view(again["message_id"])
    loop.update(after=v["options"], applied=[c for c in v["citations"] if c.get("type") == "feedback"],
                lengths={o["id"]: len(o["subject"]) for o in v["options"]}, applied_count=repo.notes[rule["id"]]["applied_count"])
report["memory_loop"] = loop
print("\nMEMORY LOOP", json.dumps(loop, ensure_ascii=False, default=str)[:1500])

OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print("saved", OUT)
