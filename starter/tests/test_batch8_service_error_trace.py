"""批次 8 回归测试：非 LLM 异常必须留痕（缺陷 24）。

`Service._answer` 结尾的裸 `except Exception` 是一张安全网：不管里面出什么事，
接口都给一句"抱歉，我暂时无法回答"。但它**只返回、不记录**——trace 里既没有
`errors[]`，也没有任何失败 step。于是"真出了 bug"和"这题本来就不会"在外观上
完全一样，现场排查会直接走错层。

这里钉住三件事：
1. 非 LLM 异常必须写进 `trace.errors`，且 `where == "service"`（修复前为红）；
2. 带了异常之后，对外行为不变——仍然返回 refusal，不把异常抛给调用方；
3. LLM 失败那条路仍然是 `where == "llm"` 且带 `answer_live_failed` step，
   不能被这次修改顺手改坏。

修复前预期：`test_non_llm_error_is_recorded_in_trace` 红（errors 为空）。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kbqa import service as service_module  # noqa: E402
from kbqa.config import Settings  # noqa: E402
from kbqa.llm import LLMError  # noqa: E402
from kbqa.service import Service  # noqa: E402

WORKSPACE = ROOT.parent


def make_service(tmp_path: Path, live: bool = False) -> Service:
    settings = Settings(
        data_dir=WORKSPACE / "data",
        kb_dir=WORKSPACE / "knowledge_base",
        var_dir=tmp_path / "var",
        today=date(2026, 9, 1),
        llm_base_url="http://127.0.0.1:9/fake" if live else "",
        llm_api_key="fake-key" if live else "",
        llm_model="fake-model" if live else "",
        llm_timeout=120.0,
        chat_budget=150.0,
    )
    return Service(settings)


# ---------------------------------------------------------------------------
# 缺陷 24：非 LLM 异常被裸 except 吞掉，trace 里一点痕迹都没有
# ---------------------------------------------------------------------------


def test_non_llm_error_is_recorded_in_trace(tmp_path, monkeypatch):
    """真出 bug 时必须留痕：where=service + 异常类型 + 消息。"""
    svc = make_service(tmp_path)

    def boom(plan, trace):
        raise RuntimeError("工具层炸了：模拟一个非 LLM 异常")

    monkeypatch.setattr(svc.answerer, "answer", boom)

    payload = svc.chat(None, "五月净营业额是多少？")
    trace = svc.get_trace(payload["trace_id"])

    assert payload["answer_type"] == "refusal"
    assert trace["errors"], "非 LLM 异常被吞了：trace.errors 是空的，现场无法定位"
    first = trace["errors"][0]
    assert first["where"] == "service"
    assert first["type"] == "RuntimeError"
    assert "工具层炸了" in first["message"]
    assert first["traceback"], "堆栈也要留下，否则只能看到类型看不到现场"


def test_refusal_still_returned_without_raising(tmp_path, monkeypatch):
    """安全网本身不许拆：异常不许漏给调用方，答案文本保持不变。"""
    svc = make_service(tmp_path)

    def boom(plan, trace):
        raise KeyError("store_id")

    monkeypatch.setattr(svc.answerer, "answer", boom)

    payload = svc.chat(None, "五月净营业额是多少？")

    assert payload["answer"] == "抱歉，我暂时无法回答。"
    assert payload["answer_type"] == "refusal"


def test_llm_error_path_still_marks_llm(tmp_path, monkeypatch):
    """LLM 失败那条路不能被带坏：where=llm + answer_live_failed step。"""
    svc = make_service(tmp_path, live=True)

    class BoomEngine:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def answer(self, plan, trace, history):
            raise LLMError("timeout", "假超时")

    monkeypatch.setattr(service_module, "LiveEngine", BoomEngine)

    payload = svc.chat(None, "五月净营业额是多少？")
    trace = svc.get_trace(payload["trace_id"])

    assert payload["answer_type"] == "refusal"
    assert [e["where"] for e in trace["errors"]] == ["llm"]
    assert any(s["step"] == "answer_live_failed" for s in trace["steps"])
