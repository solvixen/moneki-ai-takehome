"""批次 3 回归测试：追问链路（session 隔离 + history 传递 + 价格路由）。

Service 级测试使用真实 data/ 与 knowledge_base/（只读），var 落在临时目录，
不依赖任何预生成产物。数字断言用公开题库金标（6 月 156757 / 7 月 162414）。
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
from kbqa.sessions import SessionStore  # noqa: E402

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


# ---------------------------------------------------------------------------
# 缺陷 C1：SessionStore 无视 session_id，全局共用一份历史
# ---------------------------------------------------------------------------

def test_session_store_isolates_and_prunes():
    store = SessionStore(max_turns=2)
    store.append("A", {"q": 1})
    store.append("A", {"q": 2})
    store.append("A", {"q": 3})
    store.append("B", {"q": "x"})
    assert [t["q"] for t in store.history("A")] == [2, 3], "每个会话各自裁剪到 max_turns"
    assert [t["q"] for t in store.history("B")] == ["x"], "B 的历史里不能混进 A 的轮次"
    assert store.history(None) == [], "None 会话独立成桶"
    assert store.history("missing") == [], "不存在的会话返回空历史"


def test_chat_sessions_do_not_cross(tmp_path):
    svc = make_service(tmp_path)
    svc.chat("A", "6 月的净营业额是多少？")
    # B 没有自己的上文：同样的追问必须反问，不能偷 A 会话的上下文
    b = svc.chat("B", "那 7 月呢？")
    assert b["answer_type"] == "clarify", (
        "不同 session_id 不能串线：B 没有上文，应反问而不是拿 A 的上下文回答"
    )
    # A 自己追问必须接上：继承“净营业额”指标，给出 7 月数字
    a = svc.chat("A", "那 7 月呢？")
    assert a["answer_type"] in ("data", "hybrid"), a["answer"][:80]
    assert "162414" in a["answer"], "追问应继承上文指标，算出 7 月净营业额 162414"
    assert a["data_evidence"], "数字回答必须带 data_evidence"


def test_planner_receives_history(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    seen: dict = {}
    real_plan = svc.planner.plan

    def spy(question, history=None):
        seen["first_call_history"] = seen.get("first_call_history", history)
        seen["last_history"] = history
        return real_plan(question, history)

    monkeypatch.setattr(svc.planner, "plan", spy)
    svc.chat("A", "6 月的净营业额是多少？")
    svc.chat("A", "那 7 月呢？")
    assert seen["first_call_history"] == [], "第一轮没有上文，history 应为空"
    assert seen["last_history"], "第二轮必须把上一轮的 history 传给 planner.plan"


# ---------------------------------------------------------------------------
# 缺陷 C3：“现在多少钱一份”里的“多少”把 price 意图打回 data/summary，
# 走取数 → 区间外 refusal。价格问题该去知识库（调价文档）。
# ---------------------------------------------------------------------------

def test_price_question_routes_to_doc_not_data(tmp_path):
    svc = make_service(tmp_path)
    plan = svc.planner.plan("牛肉poke 现在多少钱一份？")
    assert plan.intent in ("doc", "hybrid"), (
        "价格问题应路由到文档（intent=%s, refusal=%s）" % (plan.intent, plan.refusal)
    )
    assert plan.refusal is None, "“现在”问的是当前售价，不应按数据区间外拒绝"


def test_followup_on_price_inherits_product(tmp_path):
    svc = make_service(tmp_path)
    first = svc.chat("C", "牛肉poke 现在多少钱一份？")
    assert first["answer_type"] in ("doc", "hybrid"), first["answer"][:80]
    second = svc.chat("C", "那 6 月 18 号那天呢？")
    assert second["answer_type"] in ("doc", "hybrid"), second["answer"][:80]
    assert second["citations"], "追问具体日期的售价应给出文档引用"


# ---------------------------------------------------------------------------
# 缺陷 C4：文档会话里的追问被“多少”降级回取数。
# “供应商后来赔了多少”接着上一轮的停售话题，答案在供应商邮件（KB-022）里，
# 但合成问题同时含上轮的“为什么”与本轮的“多少”，关键词打架后“多少”赢
# → intent=data → 拿销量当答案。
# ---------------------------------------------------------------------------

DOC_HISTORY = [
    {
        "question": "三文鱼poke 七月初为什么停售了？",
        "standalone": "三文鱼poke 七月初为什么停售了？",
        "slots": {"kind": "doc", "metric": "net_revenue"},
    }
]


def test_doc_followup_stays_doc(tmp_path):
    svc = make_service(tmp_path)
    plan = svc.planner.plan("供应商后来赔了多少？", history=DOC_HISTORY)
    assert plan.intent in ("doc", "hybrid"), (
        "文档话题的追问应继承 doc 意图，实际 intent=%s refusal=%s"
        % (plan.intent, plan.refusal)
    )


def test_plain_data_followup_unaffected(tmp_path):
    """对照：数字会话里的追问不受影响（仍走取数）。"""
    svc = make_service(tmp_path)
    plan = svc.planner.plan(
        "那 7 月呢？",
        history=[
            {
                "question": "6 月的净营业额是多少？",
                "standalone": "6 月的净营业额是多少？",
                "slots": {"kind": "summary", "metric": "net_revenue"},
            }
        ],
    )
    assert plan.intent in ("data", "hybrid"), plan.intent
