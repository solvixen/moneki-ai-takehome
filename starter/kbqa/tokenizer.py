"""分词。"""

from __future__ import annotations

import re
import unicodedata

#: 分词规则变了，索引缓存必须失效。
TOKENIZER_VERSION = "tokenizer-3"

#: 一段 ASCII 词（英文、数字、编号）或一段连续汉字。分词按这个粒度扫。
_WORD = re.compile(r"[0-9a-z_]+|[\u4e00-\u9fff]+")

#: 中文里几乎不携带信息的字。只用在“查询覆盖率”上，索引照常保留全部词。
STOP_CHARS = frozenset("的了吗呢是在有和与及或就都也还把被给对从向于个些这那哪什么怎样如何多少几请帮我你他它可以能要想会一下少吧啊呀们么样过得着为所")
STOP_WORDS = frozenset("the a an of to in is are and or for on at it this that how what".split())


def normalise(text: str) -> str:
    """全角转半角、统一大小写，比较与分词都走这一层。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def tokenize(text: str) -> list[str]:
    """英文/数字按词切，中文按二元组（bigram）切。

    中文没有空白边界，按空白切会把整句话变成一个巨 token，BM25 无从命中。
    不引第三方分词库（保持零依赖），用二元组近似词粒度：“退款政策”
    → 退款 / 款政 / 政策，查询与索引两侧对称，命中率对词级分词损失很小。
    """
    tokens: list[str] = []
    for match in _WORD.finditer(normalise(text)):
        piece = match.group()
        if piece[0].isascii() or len(piece) == 1:
            tokens.append(piece)
        else:
            tokens.extend(piece[i : i + 2] for i in range(len(piece) - 1))
    return tokens


def content_tokens(text: str) -> list[str]:
    """去掉虚词之后的查询词，用来算“这个问题被文档覆盖了多少”。"""
    kept = []
    for token in tokenize(text):
        if token in STOP_WORDS:
            continue
        if all(char in STOP_CHARS for char in token):
            continue
        kept.append(token)
    return kept
