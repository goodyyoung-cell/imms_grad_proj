"""직원이 관리하는 리드 시트 읽기: CSV, 엑셀(.xlsx), 구글 시트.

열 이름은 사람마다 다르게 쓰므로 한국어·영어 별칭을 표준 필드로 맞춘다 (HEADER_ALIASES).
첫 줄이 열 이름이어야 한다. 모르는 열은 무시하고, 결과에 알려 준다.

구글 시트는 '링크가 있는 모든 사용자' 공개 없이 읽는다.
  - Cloud Run: 서비스 계정 이메일에 시트를 '보기 권한'으로 공유하면 된다
  - 로컬: `gcloud auth application-default login --scopes=https://www.googleapis.com/auth/spreadsheets.readonly,https://www.googleapis.com/auth/cloud-platform`
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

import httpx

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("이름", "성명", "담당자", "담당자명", "고객명", "name", "contact", "contactname", "fullname"),
    "email": ("이메일", "메일", "이메일주소", "email", "e-mail", "emailaddress", "mail"),
    "phone": ("전화", "전화번호", "연락처", "휴대폰", "핸드폰", "phone", "mobile", "tel"),
    "company_name": ("회사", "회사명", "기업", "기업명", "업체", "업체명", "고객사", "company", "companyname", "account", "organization"),
    "company_domain": ("홈페이지", "웹사이트", "도메인", "회사홈페이지", "website", "domain", "url", "homepage"),
    "biz_reg_no": ("사업자등록번호", "사업자번호", "bizregno", "businessnumber"),
    "title": ("직급", "직책", "직함", "직위", "title", "jobtitle", "position", "role"),
    "source": ("유입경로", "유입", "출처", "경로", "유입유형", "source", "leadsource", "channel"),
    "inbound_at": ("유입일", "등록일", "접수일", "날짜", "일자", "date", "createdat", "inboundat"),
    "notes": ("메모", "비고", "문의내용", "내용", "notes", "note", "memo", "comment", "comments"),
}
_LOOKUP = {alias: field for field, aliases in HEADER_ALIASES.items() for alias in aliases}


def _key(header: Any) -> str:
    return re.sub(r"[\s_()\-:/.]", "", str(header or "")).lower()


def map_rows(headers: list[Any], rows: list[list[Any]]) -> tuple[list[dict], dict]:
    """첫 줄(열 이름)과 나머지 줄을 표준 필드 dict 목록으로. 빈 줄은 건너뛴다."""
    mapping: dict[int, str] = {}
    unknown: list[str] = []
    for i, h in enumerate(headers):
        field = _LOOKUP.get(_key(h)) or _LOOKUP.get(_key(h).replace("-", ""))
        if field and field not in mapping.values():
            mapping[i] = field
        elif str(h or "").strip():
            unknown.append(str(h).strip())
    if "company_name" not in mapping.values():
        raise ValueError(f"회사명 열을 찾지 못했습니다. 열 이름: {[str(h) for h in headers]}. "
                         f"'회사명' 같은 이름으로 바꿔 주세요.")
    out = []
    for n, row in enumerate(rows, start=2):  # 시트의 실제 줄 번호 (1번 줄은 열 이름)
        values = {mapping[i]: (v.strip() if isinstance(v, str) else v) for i, v in enumerate(row) if i in mapping}
        if not any(v not in (None, "") for v in values.values()):
            continue
        out.append({**values, "_row": n})
    return out, {"columns": {headers[i]: f for i, f in mapping.items()}, "ignored_columns": unknown}


def read_csv_bytes(data: bytes) -> tuple[list[dict], dict]:
    for enc in ("utf-8-sig", "cp949"):  # 엑셀에서 저장한 한글 CSV는 cp949인 경우가 많다
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("CSV 인코딩을 알 수 없습니다 (UTF-8 또는 CP949로 저장해 주세요)")
    table = list(csv.reader(io.StringIO(text)))
    if not table:
        raise ValueError("빈 파일입니다")
    return map_rows(table[0], table[1:])


def read_xlsx_bytes(data: bytes, sheet: str | None = None) -> tuple[list[dict], dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    table = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    if not table:
        raise ValueError("빈 시트입니다")
    return map_rows(table[0], table[1:])


def read_file(path: Path | str, sheet: str | None = None) -> tuple[list[dict], dict]:
    p = Path(path)
    data = p.read_bytes()
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        return read_xlsx_bytes(data, sheet)
    if p.suffix.lower() in (".csv", ".txt"):
        return read_csv_bytes(data)
    raise ValueError(f"지원하지 않는 파일 형식입니다: {p.suffix} (.csv, .xlsx)")


def read_upload(filename: str, data: bytes, sheet: str | None = None) -> tuple[list[dict], dict]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        return read_xlsx_bytes(data, sheet)
    if filename.lower().endswith((".csv", ".txt")):
        return read_csv_bytes(data)
    raise ValueError("CSV 또는 엑셀(.xlsx) 파일만 올릴 수 있습니다")


def spreadsheet_id(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def read_google_sheet(url_or_id: str, sheet: str | None = None, http: httpx.Client | None = None,
                      token: str | None = None) -> tuple[list[dict], dict]:
    """구글 시트를 Sheets API로 읽는다. 시트를 서비스 계정(또는 로그인 계정)에 공유해야 한다."""
    if token is None:
        import google.auth
        from google.auth.transport.requests import Request

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        creds.refresh(Request())
        token = creds.token
    sid = spreadsheet_id(url_or_id)
    rng = sheet or "A:Z"
    client = http or httpx.Client(timeout=30)
    resp = client.get(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{rng}",
                      params={"valueRenderOption": "FORMATTED_VALUE"}, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code in (403, 404):
        raise PermissionError("시트를 읽을 수 없습니다. 서비스 계정 이메일에 시트를 '보기 권한'으로 공유했는지 확인하세요.")
    resp.raise_for_status()
    values = resp.json().get("values", [])
    if not values:
        raise ValueError("빈 시트입니다")
    width = max(len(r) for r in values)
    rows = [r + [""] * (width - len(r)) for r in values[1:]]
    return map_rows(values[0] + [""] * (width - len(values[0])), rows)
