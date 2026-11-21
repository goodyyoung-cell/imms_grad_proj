"""OpenDART 클라이언트 (기업개황, 최근 공시, 주요 재무).

API 문서: https://opendart.fss.or.kr/guide/main.do
비상장 소기업은 대부분 DART에 없다. 그 경우 에이전트가 홈페이지 등 다른 출처로 넘어간다.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import httpx

BASE = "https://opendart.fss.or.kr/api"
TIMEOUT = 30


def _norm(name: str) -> str:
    n = name.replace("(주)", "").replace("㈜", "").replace("주식회사", "")
    return "".join(n.split()).lower()


class DartClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=TIMEOUT)

    def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        resp = self.http.get(f"{BASE}/{path}", params={"crtfc_key": self.api_key, **params})
        resp.raise_for_status()
        data = resp.json()
        # 000: 정상, 013: 조회된 데이터 없음
        if data.get("status") not in ("000", "013"):
            raise RuntimeError(f"DART 오류 {data.get('status')}: {data.get('message')}")
        return data

    @lru_cache(maxsize=1)
    def _corp_codes(self) -> list[dict[str, str]]:
        """전체 회사 고유번호 목록 (zip 안의 XML). 처음 한 번만 받는다."""
        resp = self.http.get(f"{BASE}/corpCode.xml", params={"crtfc_key": self.api_key})
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
        root = ET.fromstring(xml_bytes)
        return [
            {
                "corp_code": el.findtext("corp_code", ""),
                "corp_name": el.findtext("corp_name", ""),
                "stock_code": (el.findtext("stock_code", "") or "").strip(),
            }
            for el in root.iter("list")
        ]

    def search_company(self, name: str, limit: int = 5) -> list[dict[str, str]]:
        """회사명으로 후보 찾기. 정확히 같은 이름을 먼저, 상장사를 먼저 보여 준다."""
        q = _norm(name)
        hits = [c for c in self._corp_codes() if q and q in _norm(c["corp_name"])]
        hits.sort(key=lambda c: (_norm(c["corp_name"]) != q, c["stock_code"] == ""))
        return hits[:limit]

    def company(self, corp_code: str) -> dict[str, Any]:
        data = self._get_json("company.json", corp_code=corp_code)
        keys = ["corp_name", "ceo_nm", "corp_cls", "induty_code", "est_dt", "hm_url", "adres", "jurir_no", "bizr_no"]
        return {k: data.get(k) for k in keys} | {"source_url": f"{BASE}/company.json?corp_code={corp_code}"}

    def recent_disclosures(self, corp_code: str, days: int = 180, limit: int = 5) -> list[dict[str, str]]:
        bgn = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
        data = self._get_json("list.json", corp_code=corp_code, bgn_de=bgn, page_count=limit)
        return [
            {
                "date": r.get("rcept_dt"),
                "title": r.get("report_nm"),
                "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.get('rcept_no')}",
            }
            for r in data.get("list", [])[:limit]
        ]

    def key_financials(self, corp_code: str, year: int | None = None) -> dict[str, Any]:
        """사업보고서 주요 계정에서 매출액과 영업이익. 단위는 억 원."""
        year = year or date.today().year - 1
        data = self._get_json("fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=str(year), reprt_code="11011")
        rows = data.get("list", [])
        # 연결재무제표(CFS)가 있으면 우선, 없으면 개별(OFS)
        fs = "CFS" if any(r.get("fs_div") == "CFS" for r in rows) else "OFS"

        def amount(account: str) -> float | None:
            for r in rows:
                if r.get("fs_div") == fs and r.get("account_nm") == account:
                    raw = (r.get("thstrm_amount") or "").replace(",", "").strip()
                    if raw and raw != "-":
                        return int(raw) / 1e8
            return None

        revenue, op_income = amount("매출액"), amount("영업이익")
        margin = round(op_income / revenue * 100, 1) if revenue and op_income is not None else None
        return {
            "year": year,
            "revenue_100m_krw": round(revenue, 1) if revenue is not None else None,
            "operating_margin_pct": margin,
            "source_url": f"{BASE}/fnlttSinglAcnt.json?corp_code={corp_code}&bsns_year={year}&reprt_code=11011",
        }
