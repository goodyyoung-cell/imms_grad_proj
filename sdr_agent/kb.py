"""승인된 자료(지식베이스) 검색.

제품·가격·정책에 관한 문장은 여기서 찾은 조각을 근거로만 쓴다. 근거가 없으면 모른다고 하고 사람에게 넘긴다.

지금은 sdr_agent/knowledge/*.md 를 '## 제목' 단위 조각으로 나누고, 한국어에 맞게 글자 2-gram 겹침으로
점수를 매기는 키워드 검색이다. 임베딩 키가 필요 없고 결과를 설명하기 쉽다.
pgvector로 바꿀 때는 같은 search() 인터페이스를 가진 클래스를 만들어 바꿔 끼운다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

# 이 점수 미만이면 근거가 없다고 본다
MIN_SCORE = 0.08


@dataclass(frozen=True)
class Chunk:
    id: str        # 예: "02_pricing#플랜"
    doc: str       # 문서 제목
    heading: str
    text: str


def _bigrams(text: str) -> set[str]:
    s = re.sub(r"[^0-9a-z가-힣]+", " ", text.lower())
    grams: set[str] = set()
    for word in s.split():
        if len(word) == 1:
            grams.add(word)
        grams.update(word[i:i + 2] for i in range(len(word) - 1))
    return grams


def split_markdown(stem: str, text: str) -> list[Chunk]:
    doc_title = stem
    chunks: list[Chunk] = []
    heading, buf = "", []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if heading and body:
            chunks.append(Chunk(f"{stem}#{heading}", doc_title, heading, body))

    for line in text.splitlines():
        if line.startswith("# "):
            doc_title = line[2:].strip()
        elif line.startswith("## "):
            flush()
            heading, buf = line[3:].strip(), []
        elif heading:
            buf.append(line)
    flush()
    return chunks


class KnowledgeBase:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._grams = [_bigrams(f"{c.doc} {c.heading} {c.text}") for c in chunks]
        # 여러 조각에 흔한 2-gram("있나", "나요")은 가볍게, 드문 2-gram은 무겁게 (IDF)
        n = len(chunks)
        df: dict[str, int] = {}
        for grams in self._grams:
            for g in grams:
                df[g] = df.get(g, 0) + 1
        self._idf = {g: math.log((n + 1) / (d + 0.5)) for g, d in df.items()}
        self._unknown_idf = math.log((n + 1) / 0.5)

    @classmethod
    def from_dir(cls, path: Path = KNOWLEDGE_DIR) -> "KnowledgeBase":
        chunks: list[Chunk] = []
        for f in sorted(path.glob("*.md")):
            chunks.extend(split_markdown(f.stem, f.read_text(encoding="utf-8")))
        return cls(chunks)

    def get(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.id == chunk_id), None)

    def search(self, query: str, k: int = 3) -> list[dict]:
        q = _bigrams(query)
        if not q:
            return []
        weight = {g: self._idf.get(g, self._unknown_idf) for g in q}
        total = sum(weight.values())
        scored = []
        for chunk, grams in zip(self.chunks, self._grams):
            hit = sum(weight[g] for g in q & grams)
            if hit:
                scored.append((hit / total, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"id": c.id, "doc": c.doc, "heading": c.heading, "text": c.text, "score": round(s, 3)}
                for s, c in scored[:k] if s >= MIN_SCORE]


@lru_cache(maxsize=1)
def default_kb() -> KnowledgeBase:
    return KnowledgeBase.from_dir()
