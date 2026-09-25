"""批次 2 回归测试：loader 三格式与编码、中文分词、切块丢尾、doc_id 错位、先过滤再截断。

全部用临时目录里的合成知识库构造，不依赖真实 knowledge_base，
期望值（命中谁、覆盖到哪、几条结果）都能手工推出来。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from kbqa.chunker import chunk_document
from kbqa.index import build_index
from kbqa.loader import load_document, load_knowledge_base
from kbqa.retriever import Retriever
from kbqa.tokenizer import tokenize


# ---------------------------------------------------------------------------
# 缺陷 B1：loader 只认 .md，txt/html 进不了索引
# ---------------------------------------------------------------------------


def test_loader_accepts_txt_and_html(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "KB-901_退款政策.md").write_text("# 退款政策\n正文", encoding="utf-8")
    (kb / "KB-902_营业时间通知.txt").write_text("营业时间通知正文", encoding="utf-8")
    (kb / "KB-903_常见问题.html").write_text(
        "<html><head><title>常见问题</title></head><body>问答正文</body></html>",
        encoding="utf-8",
    )
    docs, _warnings = load_knowledge_base(kb)
    fmts = {d.doc_id: d.fmt for d in docs}
    assert fmts == {"KB-901": "md", "KB-902": "txt", "KB-903": "html"}


def test_gbk_txt_decoded_correctly(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    content = "营业时间调整通知：自 2026 年 8 月 15 日起，门店营业时间调整为上午十点开门。"
    # KB-062 就是这种 GBK 编码的旧 OA 导出，评测按 GBK 核对 quote。
    (kb / "KB-904_营业时间调整.txt").write_bytes(content.encode("gbk"))
    docs, _warnings = load_knowledge_base(kb)
    assert len(docs) == 1, "GBK 编码的 txt 不能被跳过"
    doc = docs[0]
    assert "营业时间" in doc.text, "GBK 按UTF-8硬解会变乱码"
    assert doc.effective_from == date(2026, 8, 15), "正文里的生效日期要能认出来"


# ---------------------------------------------------------------------------
# 缺陷 B2：tokenizer 按空白切，中文整句一个巨 token
# ---------------------------------------------------------------------------


def test_tokenize_chinese_produces_bigrams():
    tokens = tokenize("退款政策")
    assert "退款" in tokens and "款政" in tokens and "政策" in tokens
    assert "退款政策" not in tokens, "中文整句不能再作为一个 token"


def test_tokenize_mixed_ascii_and_chinese():
    tokens = tokenize("S02 门店退款")
    assert "s02" in tokens, "英文数字串照常成词"
    assert "退款" in tokens


# ---------------------------------------------------------------------------
# 缺陷 B3：chunker range(0, len-300, 300) 把每篇文档的尾巴切掉
# ---------------------------------------------------------------------------


def test_chunker_keeps_tail(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    body = "顾客申请退款后款项将在三个工作日内退回原支付渠道。" * 14  # ≈ 700 字
    (kb / "KB-905_退款政策.md").write_text("# 退款政策\n" + body, encoding="utf-8")
    doc = load_document(kb / "KB-905_退款政策.md")
    chunks = chunk_document(doc)
    joined = "".join(c.text for c in chunks)
    assert len(joined) == len(doc.text), "所有切块拼起来必须覆盖全文，不能丢尾巴"
    assert doc.text[-20:] in joined


# ---------------------------------------------------------------------------
# 缺陷 B4：retriever 把命中片段的 doc_id 用别的文档覆盖（错位赋值）
# ---------------------------------------------------------------------------


def _write_three_docs(kb: Path) -> None:
    """A 充满“退款”且长到切成多块；B 与查询无关；C 只有一小块提到退款。

    A 必须产出 >=2 块：错位赋值的分歧发生在“同文档后续块被 per-doc 限额
    跳过”之后，单块文档的排序序列与命中序列恰好对齐，缺陷不可见。
    """
    a = "顾客申请退款后款项将在三个工作日内退回原支付渠道。" * 30  # 约 720 字，3 块
    b = "员工折扣仅限周一使用。" * 60  # 约 660 字
    c = "会员日下单赠积分，退款时扣除相应积分。" + "会员日当天双倍积分可以兑换周边礼品。" * 40
    (kb / "KB-801_退款政策.md").write_text("# 退款政策\n" + a, encoding="utf-8")
    (kb / "KB-802_员工折扣.md").write_text("# 员工折扣\n" + b, encoding="utf-8")
    (kb / "KB-803_会员活动.md").write_text("# 会员活动\n" + c, encoding="utf-8")


def test_hit_doc_id_matches_its_own_chunk(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    _write_three_docs(kb)
    index = build_index(kb)
    result = Retriever(index, today=date(2026, 9, 1)).search("退款", top_k=5)
    assert result.hits, "查询“退款”应有命中"
    for hit in result.hits:
        assert hit.doc_id == hit.meta.get("doc_id"), (
            "命中片段的 doc_id 被别的文档覆盖：%r 的正文却是 %r"
            % (hit.doc_id, hit.text[:30])
        )


# ---------------------------------------------------------------------------
# 缺陷 B6：loader 元数据键名 "state" 与消费方读取的 "status" 对不上，
# 版本路由（已废止过滤）从未生效
# ---------------------------------------------------------------------------


def test_superseded_doc_is_filtered_out(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    v1 = (
        "---\ntitle: 储值政策 v1\ntype: 政策\nstatus: 已废止\n"
        "superseded_by: KB-806\neffective_from: 2026-01-01\n---\n"
        + "储值卡余额不可提现，只能到店消费。" * 20
    )
    v2 = (
        "---\ntitle: 储值政策 v2\ntype: 政策\nstatus: 现行\n"
        "effective_from: 2026-01-01\n---\n"
        + "储值卡余额支持提现到银行卡。" * 20
    )
    (kb / "KB-805_储值政策v1.md").write_text(v1, encoding="utf-8")
    (kb / "KB-806_储值政策v2.md").write_text(v2, encoding="utf-8")
    index = build_index(kb)
    result = Retriever(index, today=date(2026, 9, 1)).search("储值卡", top_k=3)
    reasons = {f["doc_id"]: f["reason"] for f in result.filtered}
    assert "KB-805" in reasons, "已废止且被取代的版本必须进入过滤名单"
    assert all(h.doc_id != "KB-805" for h in result.hits), "已废止版本不能出现在结果里"


# ---------------------------------------------------------------------------
# 缺陷 B5：allowed 没排除已过滤文档，占坑后被末端踢掉，top_k 凑不满
# ---------------------------------------------------------------------------


def test_excluded_version_does_not_eat_top_k(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    v1 = (
        "---\ntitle: 储值政策 v1\ntype: 政策\nstatus: 已废止\n"
        "superseded_by: KB-806\neffective_from: 2026-01-01\n---\n"
        + "储值卡余额不可提现，只能到店消费。" * 20
    )
    v2 = (
        "---\ntitle: 储值政策 v2\ntype: 政策\nstatus: 现行\n"
        "effective_from: 2026-01-01\n---\n"
        + "储值卡余额支持提现到银行卡。" * 60  # 多块：排除 KB-805 后场上仍要够 top_k
    )
    (kb / "KB-805_储值政策v1.md").write_text(v1, encoding="utf-8")
    (kb / "KB-806_储值政策v2.md").write_text(v2, encoding="utf-8")
    (kb / "KB-807_员工手册.md").write_text("# 员工手册\n" + "考勤打卡规定。" * 80, encoding="utf-8")
    index = build_index(kb)
    result = Retriever(index, today=date(2026, 9, 1)).search("储值卡余额", top_k=3)
    assert all(h.doc_id != "KB-805" for h in result.hits), "已废止版本不能出现在结果里"
    assert len(result.hits) == 3, "过滤发生在打分之前时，top_k 应由其余文档补齐"
