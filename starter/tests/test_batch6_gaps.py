"""批次 6：剩余 10 分的三个根因——切块劈句、表格表头、跨语言检索。

三个案子共用一个病根：chunker 按 300 字硬切，一句话会被从中间劈成两块，
后续一切"按句取证"都建立在完句假设上：
- H03：KB-028"目标销量 900 杯"被劈成"…目标销量"/"900 杯"，_TARGET 匹配不到；
- C07：KB-029"毛利率低于 35%…"的句子被劈开，答案只剩"损耗"那半句；
- C02：KB-040 的表格行引出来是原始竖线行，表头（麸质/大豆/芝麻）丢了；
- C04/R04：中文问句打全英文邮件 KB-022，检索和挑句两层都接不上。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kbqa.config import Settings  # noqa: E402
from kbqa.service import Service  # noqa: E402

WORKSPACE = ROOT.parent


def make_service(tmp_path: Path) -> Service:
    settings = Settings(
        data_dir=WORKSPACE / "data",
        kb_dir=WORKSPACE / "knowledge_base",
        var_dir=tmp_path / "var",
        today=date(2026, 9, 1),
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        llm_timeout=120.0,
        chat_budget=150.0,
    )
    return Service(settings)


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    return make_service(tmp_path_factory.mktemp("batch6"))


# --------------------------------------------------------------------------- #
# F1：切块必须尊重句子边界                                                      #
# --------------------------------------------------------------------------- #


def test_target_sentence_stays_whole(service):
    """H03：目标句"…目标销量 900 杯"必须在同一个句子里，_TARGET 才匹配得到。"""
    sentences = service.facts.sentences("KB-028")
    whole = [s for s in sentences if "目标" in s and "900" in s]
    assert whole, "目标句被切块劈开，900 杯不在'目标'同一句里：%s" % [
        s for s in sentences if "目标" in s or "900" in s
    ]


def test_cause_sentence_stays_whole(service):
    """C07：'毛利率低于 35%…损耗'必须完整，因果取证才带得上数字。"""
    sentences = service.facts.sentences("KB-029")
    whole = [s for s in sentences if "毛利率" in s and "35" in s and "损耗" in s]
    assert whole, "毛利率句被劈开：%s" % [s for s in sentences if "毛利率" in s]


def test_no_mid_sentence_chunk_seams(service):
    """通用断言：任何 chunk 的接缝都不许落在句子中间。"""
    from kbqa.units import _SENTENCE_TAIL

    for chunk in service.retriever.index.chunks:
        text = chunk.source_text
        if chunk.kind == "table" or not text:
            continue
        for line in text.splitlines():
            line = line.strip()
            if len(line) < 8 or line.endswith(_SENTENCE_TAIL) or line.endswith("|"):
                continue
            # 行末既不是句尾也不是表格线——只允许是文档的最后一行被截断
            assert chunk.chunk_id.endswith("#1") or len(text) < 300, (
                "chunk %s 在句子中间断开：%r" % (chunk.chunk_id, line[-30:])
            )


# --------------------------------------------------------------------------- #
# F2：表格行引用必须带表头                                                      #
# --------------------------------------------------------------------------- #


def test_allergen_row_rendered_with_header(service):
    """C02：牛肉poke 行渲染出来要说清含哪些过敏原，而不是一排 ✓。"""
    row = next(
        u.text
        for u in service.facts.units("KB-040")
        if "P06" in u.text and "牛肉poke" in u.text
    )
    rendered = service.facts.render("KB-040", row)
    for allergen in ("麸质", "大豆", "芝麻"):
        assert allergen in rendered, "渲染结果缺 %s：%s" % (allergen, rendered)
    assert "✓" not in rendered, "原始标记不应留在渲染结果里：%s" % rendered


# --------------------------------------------------------------------------- #
# F3：跨语言检索                                                                #
# --------------------------------------------------------------------------- #


def test_chinese_query_finds_english_email(service):
    """R04：中文问断供赔偿，KB-022（英文邮件）必须进 top5。"""
    result = service.retriever.search("三文鱼那次断供供应商赔了多少钱", top_k=5)
    docs = [hit.doc_id for hit in result.hits]
    assert "KB-022" in docs, docs


def test_english_settlement_sentence_pickable(service):
    """C04：KB-022 里写 CNY 8,600 的句子要能被挑出来当证据。"""
    ranked = service.facts.rank(
        "三文鱼那次断供，供应商最后赔了我们多少钱？", "KB-022", limit=3
    )
    assert ranked, "KB-022 里挑不出任何句子"
    joined = " ".join(text for _, text in ranked)
    assert "8,600" in joined or "8600" in joined, joined[:200]
