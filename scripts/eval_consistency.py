"""사용법: python scripts/eval_consistency.py <결과.json> [반복 횟수]  (gcloud 로그인과 Secret Manager 읽기 권한 필요)`n평가: 같은 리드로 첫 메일 초안을 3번 만들어 일관성과 근거 일치를 잰다.
가짜 DB(메모리)와 실제 LLM을 쓴다. 실서버 DB에는 아무것도 쓰지 않고 Slack으로도 보내지 않는다.
사실(facts)과 점수는 배포 서버의 읽기 전용 API에서 가져온다.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1])
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3

GCLOUD = os.environ.get("GCLOUD", "gcloud")
API = "https://sdr-agent-840381264541.asia-northeast3.run.app"


def secret(name):
    return subprocess.run([GCLOUD, "secrets", "versions", "access", "latest", "--secret", name], capture_output=True, text=True, shell=(os.name == "nt")).stdout.strip()


# 외부로 나가는 설정은 모두 비운다 (Slack, DB). 모델 키만 이 프로세스 안에서 쓴다.
for k in ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL", "SLACK_APPROVERS", "ADMIN_WEB_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "BOOKING_URL"):
    os.environ[k] = ""
os.environ["ANTHROPIC_API_KEY"] = secret("anthropic-api-key")
os.environ["ANTHROPIC_MODEL"] = "claude-sonnet-5-5"
os.environ["DEFAULT_LLM"] = "claude"
os.environ["PUBLIC_BASE_URL"] = ""

pw = secret("admin-password")


def call(path, data=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(API + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
    return json.loads(urllib.request.urlopen(r, timeout=60).read().decode())


tok = call("/auth/login", {"name": "평가", "password": pw})["token"]
SCEN = [("S1", "알테오젠"), ("S2", "삼양식품"), ("S3", "나노신소재"), ("S4", "에코프로비엠"), ("S5", "LG에너지솔루션")]
leads = {}
for sid, name in SCEN:
    q = urllib.request.quote(name)
    lst = call(f"/admin/leads?q={q}&limit=5", tok=tok)["leads"]
    lead = next(l for l in lst if l["company_name"] == name)
    leads[sid] = call(f"/admin/leads/{lead['id']}", tok=tok)

from sdr_agent import jobs  # noqa: E402
from sdr_agent.config import load_settings  # noqa: E402
from sdr_agent.kb import default_kb  # noqa: E402
from tests.test_email_loop import FakeRepo  # noqa: E402

settings = load_settings()
kb = default_kb()
UNIT = r"(?:원|%|명|개|건|일|배|억|만|개월|년)"


def nums(t):
    return sorted(set(m.strip() for m in re.findall(r"\d[\d,.]*\s?" + UNIT, t)))


norm = lambda s: re.sub(r"[\s,]", "", s)  # noqa: E731


def body_core(b):
    cut = b.find("\n\n---\n")
    return b if cut < 0 else b[:cut]


results = []
for sid, d in leads.items():
    lead = {k: d["lead"].get(k) for k in ("id", "name", "email", "company_name", "title", "source", "inbound_at", "notes", "phone")}
    lead.update(status="researched", unsubscribed_at=None)
    runs = []
    for i in range(RUNS):
        repo = FakeRepo([lead])
        repo.facts = {lead["id"]: d["facts"]}
        repo.scores = {lead["id"]: d["score"]}
        t0 = time.time()
        out = jobs.draft_emails([lead["id"]], repo=repo, settings=settings, trigger="eval")
        sec = round(time.time() - t0, 1)
        o = out[0]
        if o["status"] != "drafted":
            runs.append({"run": i + 1, "status": o["status"], "seconds": sec})
            continue
        m = repo.messages[o["message_id"]]
        cites = m["citations"]
        sup = []
        fmap = {f["id"]: f["value"] for f in d["facts"]}
        for c in cites:
            if c["type"] == "fact":
                sup.append(fmap.get(c["id"], ""))
            else:
                ch = kb.get(c["id"])
                if ch:
                    sup.append(ch.heading + " " + ch.text)
        hay = norm("\n".join(sup) + lead["company_name"])
        core = body_core(m["body"])
        n = nums(core)
        bad = [x for x in n if norm(x) not in hay]
        runs.append({"run": i + 1, "status": "drafted", "seconds": sec, "subject": m["subject"], "chars": len(core), "numbers": n,
                     "unsupported_numbers": bad, "fact_cites": sorted(c["id"] for c in cites if c["type"] == "fact"),
                     "kb_cites": sorted(c["id"] for c in cites if c["type"] == "kb"), "has_cta": "미팅" in core or "통화" in core,
                     "has_ai_notice": "AI" in m["body"], "has_optout": "수신 거부" in m["body"], "body": core})
    results.append({"scenario": sid, "company": lead["company_name"], "score": (d["score"] or {}).get("score"), "runs": runs})
    print(sid, lead["company_name"], [(r["status"], r.get("seconds")) for r in runs], flush=True)

OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved", OUT)
