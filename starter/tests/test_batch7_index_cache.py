"""批次 7 回归测试：索引缓存必须跟着知识库内容走。

背景（这是一条真实踩过的坑）：`content_key()` 早期只哈希
`INDEX_VERSION|CHUNKER_VERSION|TOKENIZER_VERSION` 三个版本号，`kb_dir`
参数收了完全没用——任何知识库都算出同一个键，`.cache/index.json` 又是
缓存命中就直接复用。于是「换一份知识库 → 重建 → 新文档永远进不了索引」
成了静默故障，而本地自测看不出来（自测环境里我们只会改代码，而改代码会
一起 bump 版本号，缓存恰好失效，问题被掩盖）。

这些测试全部走 `load_index(..., rebuild=False)`——也就是服务启动时的
真实路径——断言"内容变了，索引必须变"。修复前本文件必红。
"""

from __future__ import annotations

from pathlib import Path

from kbqa.index import content_key, load_index


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 缓存键本身：必须由知识库内容决定
# ---------------------------------------------------------------------------


def test_content_key_differs_across_knowledge_bases(tmp_path):
    """两份内容不同的知识库，缓存键绝不能相同。

    原实现的键在任意知识库上都相等（实测全等 ecf724fce9fe…），这条断言
    就是那个故障的直接反例。
    """
    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    _write(kb_a / "KB-901_政策.md", "# 政策\n退款时限 24 小时")
    _write(kb_b / "KB-901_政策.md", "# 政策\n退款时限 48 小时")
    assert content_key(kb_a) != content_key(kb_b)


def test_content_key_stable_for_identical_content(tmp_path):
    """内容一样时键必须一样，否则每次启动都白重建。"""
    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    _write(kb_a / "KB-901_政策.md", "# 政策\n退款时限 24 小时")
    _write(kb_b / "KB-901_政策.md", "# 政策\n退款时限 24 小时")
    assert content_key(kb_a) == content_key(kb_b)


def test_content_key_ignores_touch(tmp_path):
    """只改 mtime、不动内容，不该触发重建。"""
    kb = tmp_path / "kb"
    doc = kb / "KB-901_政策.md"
    _write(doc, "# 政策\n退款时限 24 小时")
    before = content_key(kb)
    doc.touch()
    assert content_key(kb) == before


def test_content_key_handles_missing_dir(tmp_path):
    """知识库目录不存在时也要返回稳定的键，不能抛异常。"""
    missing = tmp_path / "nope"
    assert content_key(missing) == content_key(tmp_path / "also_nope")


# ---------------------------------------------------------------------------
# 服务启动路径：rebuild=False 时也必须读到最新内容
# ---------------------------------------------------------------------------


def test_replaced_knowledge_base_is_picked_up(tmp_path):
    """换一份知识库（结构相同、文档有增有改）后，旧缓存不能再被复用。

    这正是评审第 3 步的动作：换掉 knowledge_base/ → 执行重建 → 跑隐藏题库。
    """
    cache = tmp_path / "cache" / "index.json"

    old_kb = tmp_path / "old_kb"
    _write(old_kb / "KB-901_政策.md", "# 政策\n旧库正文")
    first = load_index(old_kb, cache)
    assert set(first.docs_meta) == {"KB-901"}

    new_kb = tmp_path / "new_kb"
    _write(new_kb / "KB-901_政策.md", "# 政策\n新库正文")
    _write(new_kb / "KB-902_通知.md", "# 通知\n新库新增的一篇")

    second = load_index(new_kb, cache)  # rebuild=False：模拟服务启动
    assert set(second.docs_meta) == {"KB-901", "KB-902"}, "换库后必须读到新库的全部文档"
    assert "新库正文" in second.texts["KB-901"], "同编号文档的正文也必须换成新库的"


def test_added_document_enters_index(tmp_path):
    """知识库新增一篇文档，索引里必须能查到它。"""
    kb = tmp_path / "kb"
    cache = tmp_path / "index.json"
    _write(kb / "KB-901_政策.md", "# 政策\n退款时限 24 小时")
    load_index(kb, cache)

    _write(kb / "KB-999_新规.md", "# 新规\n自 2026 年 9 月 1 日起执行新规")
    after = load_index(kb, cache)
    assert "KB-999" in after.docs_meta, "新增文档必须进索引"


def test_edited_document_text_follows(tmp_path):
    """同一篇文档改了正文，索引里的文本必须跟着变。"""
    kb = tmp_path / "kb"
    cache = tmp_path / "index.json"
    doc = kb / "KB-901_政策.md"
    _write(doc, "# 政策\n退款时限 24 小时")
    load_index(kb, cache)

    _write(doc, "# 政策\n退款时限 48 小时")
    after = load_index(kb, cache)
    assert "48 小时" in after.texts["KB-901"]
    assert "24 小时" not in after.texts["KB-901"]


def test_deleted_document_leaves_index(tmp_path):
    """文档被删掉后，索引里不该还留着它。"""
    kb = tmp_path / "kb"
    cache = tmp_path / "index.json"
    _write(kb / "KB-901_政策.md", "# 政策\n正文一")
    _write(kb / "KB-902_通知.md", "# 通知\n正文二")
    load_index(kb, cache)

    (kb / "KB-902_通知.md").unlink()
    after = load_index(kb, cache)
    assert set(after.docs_meta) == {"KB-901"}


def test_rebuild_flag_forces_recompute(tmp_path):
    """`make rebuild` 传的 rebuild=True 必须无条件重算，即使缓存看着还有效。"""
    kb = tmp_path / "kb"
    cache = tmp_path / "index.json"
    _write(kb / "KB-901_政策.md", "# 政策\n正文")
    load_index(kb, cache)
    # 手动把缓存内容改坏（模拟缓存文件被环境干扰），键还是对的
    cache.write_text('{"key": "bogus", "version": "bm25-4"}', encoding="utf-8")
    after = load_index(kb, cache, rebuild=True)
    assert set(after.docs_meta) == {"KB-901"}
