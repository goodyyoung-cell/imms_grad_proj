"""지식베이스 검색 도구와 근거 확인.

메일 초안·답장 응대에서 공통으로 쓴다. 이번 실행에서 검색 결과로 실제 받은 조각 id만 근거로 인정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..agent.tools import Tool
from ..kb import KnowledgeBase, default_kb

# 숫자 + 원, 할인율 같은 가격·조건 표현. 이런 문장은 가격 자료 근거가 있어야 한다
PRICE_PATTERN = re.compile(r"\d[\d,]*\s*(원|만\s*원)|할인|무료\s*체험|가격|요금")
PRICING_DOC = "02_pricing"


class GroundingError(ValueError):
    """제출물이 근거 규칙을 어겼다. 루프가 LLM에게 이 메시지를 돌려줘 다시 쓰게 한다."""


@dataclass
class KnowledgeContext:
    kb: KnowledgeBase = field(default_factory=default_kb)
    seen_ids: set[str] = field(default_factory=set)

    def search_kb(self, query: str) -> dict:
        hits = self.kb.search(query)
        self.seen_ids.update(h["id"] for h in hits)
        if not hits:
            return {"results": [], "note": "승인된 자료에 근거가 없습니다. 이 내용은 쓰지 말고, 필요하면 사람에게 넘기세요."}
        return {"results": hits}

    def check(self, body: str, kb_ids: list[str]) -> list[str]:
        """kb_ids를 검증하고 정리해 돌려준다. 규칙 위반이면 GroundingError."""
        unseen = [i for i in kb_ids if i not in self.seen_ids]
        if unseen:
            raise GroundingError(f"이번에 search_kb로 받지 않은 자료 id입니다: {unseen}. 검색 결과의 id만 쓰세요.")
        if PRICE_PATTERN.search(body) and not any(i.startswith(PRICING_DOC) for i in kb_ids):
            raise GroundingError("가격·할인·체험 조건을 언급했지만 가격 자료(02_pricing) 근거가 없습니다. "
                                 "search_kb로 가격 자료를 찾아 kb_ids에 넣거나, 해당 문장을 빼세요.")
        return list(dict.fromkeys(kb_ids))

    def tool(self) -> Tool:
        return Tool(
            "search_kb",
            "승인된 제품 자료(소개, 가격, FAQ, 반대 의견 대응, 보안, 고객 사례)를 검색한다. "
            "제품·가격·정책에 관한 문장은 반드시 여기서 찾은 내용만 쓴다. 결과가 없으면 그 내용은 쓰지 않는다.",
            {"type": "object", "properties": {"query": {"type": "string", "description": "찾을 내용, 한국어"}},
             "required": ["query"]},
            self.search_kb,
        )
