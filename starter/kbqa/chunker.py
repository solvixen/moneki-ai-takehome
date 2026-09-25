"""把文档切成检索用的小块。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .loader import Document

#: 切块参数变了，索引缓存必须失效，所以写进缓存键里。
CHUNKER_VERSION = "chunker-4-sentence-seams"

CHUNK_SIZE = 300

_SENTENCE_SPLIT = re.compile(r"(?<=[.。！？；!?;])\s+")
_LIST_START = re.compile(r"^\s*(?:[-*>|]|#{1,6}\s|\d+[.、)]|[一二三四五六七八九十]+[、.])")
_FIELD_LINE = re.compile(r"^[^：:\s]{1,12}[：:]")


def _atoms(text: str) -> list[str]:
    """把正文切成不跨越句子边界的最小片段（带原文分隔符）。

    先把折行合并成逻辑行（英文邮件一行 ~70 字符、行尾没有句读，
    直接按行切一样会把一句话劈开），超长逻辑行再按句子切，单句超长
    才硬切。这三步保证 chunk 的接缝永远落在句读之间——下游按句取证
    的一切逻辑都建立在"一句话不会横跨两个 chunk"上（H03/C07 根因）。
    """
    logical: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if buffer:
                logical.append(buffer)
                buffer = ""
            continue
        continues = (
            len(buffer) >= 20
            and buffer[-1] not in "。！？；!?;|"
            and not buffer.lstrip().startswith("#")
            and not _LIST_START.match(line)
            and not _FIELD_LINE.match(line)
        )
        if continues:
            buffer += " " + line
        else:
            if buffer:
                logical.append(buffer)
            buffer = line
    if buffer:
        logical.append(buffer)

    atoms: list[str] = []
    for line in logical:
        if len(line) <= CHUNK_SIZE:
            atoms.append(line + "\n")
            continue
        parts = [p for p in _SENTENCE_SPLIT.split(line) if p] or [line]
        piece = ""
        for part in parts:
            if len(piece) + len(part) > CHUNK_SIZE and piece:
                atoms.append(piece + "\n")
                piece = part
            elif len(part) > CHUNK_SIZE:
                if piece:
                    atoms.append(piece + "\n")
                    piece = ""
                for i in range(0, len(part), CHUNK_SIZE):
                    atoms.append(part[i : i + CHUNK_SIZE])
            else:
                piece += part
        if piece:
            atoms.append(piece + "\n")
    return atoms


@dataclass
class Chunk:
    doc_id: str
    chunk_id: str
    text: str
    source_text: str
    heading: str = ""
    kind: str = "text"
    table_header: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source_text": self.source_text,
            "heading": self.heading,
            "kind": self.kind,
            "table_header": self.table_header,
        }


def chunk_document(document: Document) -> list[Chunk]:
    """按句子边界把文档切成 ≤300 字的块。

    旧实现按 CHUNK_SIZE 硬切，一句话会被从中间劈开：KB-028 的
    "全门店合计目标销量 900 杯"断成"…目标销量"/"900 杯"两块，
    目标抽取永远匹配不到；KB-029 的毛利率句同样遭殃。
    现在接缝只落在行/句读之间，单句超长才退回硬切。
    """
    text = document.text
    chunks: list[Chunk] = []
    piece = ""
    for atom in _atoms(text):
        if piece and len(piece) + len(atom) > CHUNK_SIZE:
            chunks.append(_mk_chunk(document, len(chunks) + 1, piece))
            piece = atom
        else:
            piece += atom
    if piece.strip():
        chunks.append(_mk_chunk(document, len(chunks) + 1, piece))
    if not chunks:
        chunks.append(_mk_chunk(document, 1, text.strip() or document.title))
    return chunks


def _mk_chunk(document: Document, number: int, piece: str) -> Chunk:
    return Chunk(
        doc_id=document.doc_id,
        chunk_id="%s#%d" % (document.doc_id, number),
        text=piece,
        source_text=piece,
        heading=document.title,
    )


def chunk_documents(documents: list[Document]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunk_document(document))
    return chunks
