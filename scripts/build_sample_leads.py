"""평가용 샘플 리드 만들기: KOSPI·KOSDAQ 시가총액 상위 100개사 중 각 50개사.

1. 네이버 증권에서 시장별 시가총액 순위를 받는다. 보통주만 남긴다 (우선주·ETF·스팩 제외).
2. 상위 100개사마다 WiseReport 기업개요에서 업종(WICS)과 홈페이지를 받는다.
3. 업종이 고르게 섞이도록, 업종을 돌아가며 시가총액이 큰 회사부터 하나씩 뽑아 50개사를 고른다.
4. 담당자 이름·이메일·연락처·직급·문의 메모는 고정 시드로 무작위 생성한다 (가상).
   이메일은 .example 예약 도메인, 전화는 010-0000-xxxx로 실제로 연결될 수 없게 만든다.
5. 중복 병합 검증용으로 같은 회사 다른 담당자 4건, 같은 사람 다른 표기 2건을 덧붙인다.

결과
  data/sample/market_cap_top100.csv   순위 원자료 스냅샷 (선정 여부 포함)
  data/sample/leads.json              cli seed가 넣는 리드

  python scripts/build_sample_leads.py
"""

from __future__ import annotations

import csv
import json
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent.parent / "data" / "sample"
SEED = 20261125
PER_MARKET = 50
TOP_N = 100
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130 Safari/537.36"}
http = httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True)


def top_common_stocks(market: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while len(out) < TOP_N:
        data = http.get(f"https://m.stock.naver.com/api/stocks/marketValue/{market}",
                        params={"page": page, "pageSize": 100}).json()
        for s in data["stocks"]:
            code, name = s["itemCode"], s["stockName"]
            if s.get("stockEndType") != "stock" or not code.endswith("0") or "스팩" in name:
                continue  # 우선주(코드 끝이 0이 아님)·ETF·스팩 제외
            out.append({"market": market, "rank": len(out) + 1, "code": code, "name": name,
                        "market_cap_100m_krw": int(s["marketValue"].replace(",", ""))})
            if len(out) == TOP_N:
                break
        page += 1
    return out


def company_profile(code: str) -> dict:
    t = http.get("https://navercomp.wisereport.co.kr/v2/company/c1020001.aspx", params={"cmp_cd": code}).text
    sector = re.search(r"WICS\s*:\s*([^<&]+)", t)
    home = re.search(r'<th[^>]*>홈페이지</th>\s*<td[^>]*>\s*<a href="([^"]+)"', t)
    return {"sector": sector.group(1).strip() if sector else "기타", "homepage": home.group(1).strip() if home else ""}


def pick_diverse(companies: list[dict], n: int) -> list[dict]:
    """업종을 돌아가며 각 업종에서 시가총액이 큰 회사부터 하나씩."""
    by_sector: dict[str, list[dict]] = {}
    for c in companies:  # 이미 시가총액 순
        by_sector.setdefault(c["sector"], []).append(c)
    sectors = sorted(by_sector, key=lambda s: by_sector[s][0]["rank"])  # 대표 회사가 큰 업종부터
    picked: list[dict] = []
    while len(picked) < n:
        for s in sectors:
            if by_sector[s] and len(picked) < n:
                picked.append(by_sector[s].pop(0))
    return sorted(picked, key=lambda c: c["rank"])


def domain(url: str) -> str | None:
    d = re.sub(r"^[a-z]+://", "", url.strip().lower()).strip().split("/")[0].removeprefix("www.")
    return d if "." in d else None


# --- 가상 담당자 ---

SURNAMES = [("김", "kim"), ("이", "lee"), ("박", "park"), ("최", "choi"), ("정", "jung"), ("강", "kang"), ("조", "cho"),
            ("윤", "yoon"), ("장", "jang"), ("임", "lim"), ("한", "han"), ("오", "oh"), ("서", "seo"), ("신", "shin"),
            ("권", "kwon"), ("황", "hwang"), ("송", "song"), ("류", "ryu"), ("홍", "hong"), ("문", "moon")]
GIVEN = [("민준", "minjun"), ("서연", "seoyeon"), ("도윤", "doyun"), ("지우", "jiwoo"), ("하준", "hajun"), ("서윤", "seoyun"),
         ("시우", "siwoo"), ("하은", "haeun"), ("지호", "jiho"), ("수아", "sua"), ("예준", "yejun"), ("지민", "jimin"),
         ("현우", "hyunwoo"), ("유진", "yujin"), ("준서", "junseo"), ("다은", "daeun"), ("건우", "gunwoo"), ("채원", "chaewon"),
         ("우진", "woojin"), ("소율", "soyul"), ("선우", "sunwoo"), ("예린", "yerin"), ("태윤", "taeyun"), ("나연", "nayeon")]
TITLES = ["CS팀장", "고객지원본부장", "고객경험(CX)팀장", "매니저", "프로", "책임", "선임", "수석", "과장", "차장", "부장", "대리",
          "실장", "상무", "전무", "Head of CS", "CX Lead", "디지털전환팀장", "콜센터 운영팀장", "고객센터장", "IT기획 파트장", "사원"]
SOURCES = ["demo_request"] * 3 + ["web_inquiry"] * 3 + ["seminar"] * 2 + ["business_card"] * 2 + ["other"]
NOTES = {
    "demo_request": ["데모 요청 메모: 고객센터 문의가 많아 응대가 밀립니다. 채팅 문의 비중이 큽니다.",
                     "데모 요청 메모: 상담원별 답변 품질 차이를 줄이고 싶습니다.",
                     "데모 요청 메모: 성수기 문의 급증 대응 방안을 찾고 있습니다.",
                     "데모 요청 메모: 기존 헬프데스크 교체를 검토 중입니다.", ""],
    "web_inquiry": ["웹 문의: 가격이 궁금합니다.", "웹 문의: 카카오톡 채널 문의도 함께 관리할 수 있나요?",
                    "웹 문의: 영어 문의도 지원하나요?", "웹 문의: 보안 정책 자료를 받고 싶습니다.", "웹 문의: 도입 기간이 얼마나 걸리나요?"],
    "seminar": ["세미나 'AI로 고객응대 줄이기' 참석. 질문 없음.", "세미나 참석. 사내 헬프데스크에 관심.",
                "웨비나 참석. 상담 자동화 사례 질문."],
    "business_card": ["전시회 명함. 메모 없음.", "전시회 부스 방문. 대략적인 소개만 들음."],
    "other": ["지인 소개. 고객 문의 응대가 많다고 함.", "파트너사 소개."],
}


def make_person(rng: random.Random, company: dict, used: set[str]) -> dict:
    while True:
        (s_ko, s_en), (g_ko, g_en) = rng.choice(SURNAMES), rng.choice(GIVEN)
        local = f"{g_en}.{s_en}"
        email = f"{local}@k{company['code']}.example"  # 회사별 가상 도메인, 발송 불가
        if email not in used:
            used.add(email)
            break
    source = rng.choice(SOURCES)
    return {"name": s_ko + g_ko, "email": email, "phone": f"010-0000-{rng.randint(0, 9999):04d}",
            "title": rng.choice(TITLES), "source": source, "days_ago": rng.randint(0, 14),
            "notes": rng.choice(NOTES[source]) or None}


def main() -> None:
    today = date.today().isoformat()
    rng = random.Random(SEED)
    snapshot, picked = [], []
    for market in ("KOSPI", "KOSDAQ"):
        companies = top_common_stocks(market)
        for c in companies:
            c.update(company_profile(c["code"]))
            time.sleep(0.25)
            print(f"{market} {c['rank']:>3} {c['name']} · {c['sector']} · {c['homepage']}", file=sys.stderr)
        chosen = pick_diverse(companies, PER_MARKET)
        chosen_codes = {c["code"] for c in chosen}
        for c in companies:
            snapshot.append({**c, "selected": c["code"] in chosen_codes, "as_of": today})
        picked += chosen

    used: set[str] = set()
    leads = []
    for i, c in enumerate(picked, start=1):
        leads.append({"key": f"L{i:03d}", "company_name": c["name"], "company_domain": domain(c["homepage"]),
                      **make_person(rng, c, used)})

    # 중복 병합 검증용: 같은 회사 다른 담당자 4건, 같은 사람 다른 표기 2건
    extra = []
    for base in rng.sample(leads, 4):
        c = next(p for p in picked if p["name"] == base["company_name"])
        extra.append({"key": f"D{len(extra) + 1:02d}", "company_name": base["company_name"],
                      "company_domain": base["company_domain"], **make_person(rng, c, used),
                      "merged_into_key": base["key"]})
    for base in rng.sample([l for l in leads if l["key"] not in {e["merged_into_key"] for e in extra}], 2):
        extra.append({**base, "key": f"D{len(extra) + 1:02d}", "email": base["email"].upper(),
                      "company_name": f"{base['company_name']}(주)", "source": "seminar",
                      "days_ago": max(0, base["days_ago"] - 1), "notes": "세미나 참석. 같은 사람이 다른 경로로 다시 들어옴.",
                      "merged_into_key": base["key"]})

    (OUT / "leads.json").write_text(
        "[\n" + ",\n".join("  " + json.dumps(l, ensure_ascii=False) for l in leads + extra) + "\n]\n", encoding="utf-8")
    with (OUT / "market_cap_top100.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["as_of", "market", "rank", "code", "name", "market_cap_100m_krw",
                                          "sector", "homepage", "selected"])
        w.writeheader()
        w.writerows(snapshot)
    sectors = {m: len({c["sector"] for c in picked if c["market"] == m}) for m in ("KOSPI", "KOSDAQ")}
    print(f"리드 {len(leads)}건 + 중복 {len(extra)}건, 업종 수 {sectors}", file=sys.stderr)


if __name__ == "__main__":
    main()
