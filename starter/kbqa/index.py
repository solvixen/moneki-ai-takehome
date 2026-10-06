"""纯 Python 的 BM25 索引，带一个磁盘缓存。"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .aliases import AliasTable, build_alias_table
from .chunker import CHUNKER_VERSION, Chunk, chunk_documents
from .loader import SUPPORTED_SUFFIXES, Document, load_knowledge_base
from .tokenizer import TOKENIZER_VERSION, tokenize

INDEX_VERSION = "bm25-4"
K1 = 1.5
B = 0.75


def knowledge_fingerprint(kb_dir: Path) -> str:
    """知识库内容指纹：每个文档的「相对路径 + 内容哈希」，按路径排序后串起来。

    只看文件字节，不解析内容——文件增、删、改名、改一个字，指纹都会变。
    """
    digest = hashlib.sha256()
    if not kb_dir.exists():
        digest.update(b"<missing>")
        return digest.hexdigest()
    for path in sorted(kb_dir.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        digest.update(path.relative_to(kb_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def content_key(kb_dir: Path) -> str:
    """缓存键 = 三个版本号 + 知识库内容指纹。

    版本号负责「解析逻辑变了」（改分词/切块/索引结构），内容指纹负责
    「知识库变了」（换一份库、加文档、改正文）。两者缺一不可：只哈希
    版本号的话，换任何一份知识库算出来的键都完全相同，缓存会被原样复用，
    新文档永远进不了索引。
    """
    digest = hashlib.sha256()
    digest.update(("%s|%s|%s\n" % (INDEX_VERSION, CHUNKER_VERSION, TOKENIZER_VERSION)).encode())
    digest.update(knowledge_fingerprint(kb_dir).encode("ascii"))
    return digest.hexdigest()


@dataclass
class Posting:
    chunk_index: int
    freq: int


class BM25Index:
    """只依赖标准库的 BM25，几十篇文档够快了。"""

    def __init__(
        self,
        chunks: list[Chunk],
        docs_meta: dict[str, dict],
        aliases: AliasTable,
        key: str,
        warnings: Optional[list[str]] = None,
        texts: Optional[dict[str, str]] = None,
    ) -> None:
        self.chunks = chunks
        self.docs_meta = docs_meta
        #: doc_id -> 文档可见正文全文。引用要逐字核对，必须留着原文。
        self.texts = texts or {}
        self.aliases = aliases
        self.key = key
        self.warnings = warnings or []
        self.doc_freq: dict[str, int] = {}
        self.postings: dict[str, list[Posting]] = {}
        self.lengths: list[int] = []
        self._build()

    def _tokens_of(self, chunk: Chunk) -> list[str]:
        """入库前做一次别名归一：英文邮件里的 Salmon 也带上“三文鱼poke”的词。

        两边都归一到数据库的写法，中文问句才有机会命中英文文档。
        """
        tokens = tokenize(chunk.text)
        lowered = chunk.text.lower()
        for canonical in self.aliases.strict_mentions(chunk.text):
            if canonical.lower() not in lowered:
                tokens.extend(tokenize(canonical))
        return tokens

    def _build(self) -> None:
        for position, chunk in enumerate(self.chunks):
            counts = Counter(self._tokens_of(chunk))
            self.lengths.append(sum(counts.values()) or 1)
            for term, freq in counts.items():
                self.postings.setdefault(term, []).append(Posting(position, freq))
                self.doc_freq[term] = self.doc_freq.get(term, 0) + 1
        self.avg_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0

    # -- 检索 -------------------------------------------------------------------

    def idf(self, term: str) -> float:
        n = len(self.chunks)
        df = self.doc_freq.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score_terms(
        self, weights: dict[str, float], allowed: Optional[set[int]] = None
    ) -> dict[int, float]:
        """返回 {块下标: 分数}。`allowed` 是元数据过滤之后还留在场上的块。"""
        scores: dict[int, float] = {}
        for term, weight in weights.items():
            idf = self.idf(term)
            if idf <= 0:
                continue
            for posting in self.postings.get(term, ()):
                if allowed is not None and posting.chunk_index not in allowed:
                    continue
                length = self.lengths[posting.chunk_index]
                tf = posting.freq
                part = idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self.avg_length))
                scores[posting.chunk_index] = scores.get(posting.chunk_index, 0.0) + part * weight
        return scores

    @property
    def max_idf(self) -> float:
        return math.log(1 + (len(self.chunks) + 0.5) / 0.5)

    def _weight_of(self, term: str) -> float:
        """整个语料里都没有的词，权重按最大 IDF 算。

        这正是“知识库里根本没提过这件事”的信号：工资、天气这类问题的词
        在语料里一个都找不到，覆盖率会掉到很低，据此拒答而不是硬答。
        """
        return self.idf(term) if self.doc_freq.get(term) else self.max_idf

    def coverage(self, terms: list[str], chunk_index: int) -> float:
        """查询里有多少（按 IDF 加权的）词真的出现在这个块里。"""
        if not terms:
            return 0.0
        chunk_terms = set(self._tokens_of(self.chunks[chunk_index]))
        unique = set(terms)
        total = sum(self._weight_of(term) for term in unique) or 1.0
        hit = sum(self._weight_of(term) for term in unique if term in chunk_terms)
        return hit / total

    def chunks_of(self, doc_id: str) -> list[Chunk]:
        return [chunk for chunk in self.chunks if chunk.doc_id == doc_id]

    # -- 持久化 -----------------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "version": INDEX_VERSION,
            "docs": self.docs_meta,
            "aliases": self.aliases.to_json(),
            "warnings": self.warnings,
            "texts": self.texts,
            "chunks": [chunk.as_dict() for chunk in self.chunks],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "BM25Index":
        chunks = [Chunk(**item) for item in payload["chunks"]]
        return cls(
            chunks=chunks,
            docs_meta=payload["docs"],
            aliases=AliasTable.from_json(payload.get("aliases") or {}),
            key=payload.get("key", ""),
            warnings=payload.get("warnings") or [],
            texts=payload.get("texts") or {},
        )


def build_index(kb_dir: Path) -> BM25Index:
    documents, warnings = load_knowledge_base(kb_dir)
    chunks = chunk_documents(documents)
    docs_meta = {document.doc_id: document.meta() for document in documents}
    aliases = build_alias_table(documents)
    texts = {document.doc_id: document.text for document in documents}
    return BM25Index(chunks, docs_meta, aliases, content_key(kb_dir), warnings, texts)


def save_index(index: BM25Index, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(index.to_json(), handle, ensure_ascii=False)


def load_index(kb_dir: Path, path: Path, rebuild: bool = False) -> BM25Index:
    """缓存命中就直接用，内容对不上就重建。"""
    key = content_key(kb_dir)
    if not rebuild and path.exists():
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("key") == key and payload.get("version") == INDEX_VERSION:
                return BM25Index.from_json(payload)
        except (ValueError, KeyError, TypeError):
            pass
    index = build_index(kb_dir)
    save_index(index, path)
    return index


def documents_of(index: BM25Index) -> list[Document]:  # pragma: no cover - 调试辅助
    return list(index.docs_meta.values())
