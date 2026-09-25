"""批次 5：LLM 接入回归测试。

不联网、不花钱：每个测试自带一个几行实现的假模型（http.server 线程），
按场景返回工具调用 / 正常回答 / 各类异常，驱动真实 Service 走完整链路。
断言口径与 eval/llm_gateway.py preflight 的 14 项检查一致：

- P8/P9：无论模型出什么事，chat 都给结构化回答，answer 非空；
- P10：思考内容（reasoning_content 里的标记）不进 answer/citations/data_evidence；
- P13：带 tools 的多轮，assistant 消息整条回传（含 reasoning_content），
  否则假模型按 DeepSeek 文档返回 400；
- 180 秒预算 / 120 秒单次超时：hang 场景用短超时验证同样走 refusal。
"""

from __future__ import annotations

import contextlib
import datetime
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kbqa.config import Settings
from kbqa.service import Service

_PROJECT = Path(__file__).resolve().parent.parent          # starter/
_WORKSPACE = _PROJECT.parent                                # 作业包根（data/、knowledge_base/ 的上层）

# --------------------------------------------------------------------------- #
# 假模型：按场景返回 OpenAI Chat Completions 形状的响应                          #
# --------------------------------------------------------------------------- #

_THINK = "思考标记 RSN-test-marker-内部推演"
_FIXED = "发票开具：请提供订单号与抬头，开好后在订单页下载。"

_TOOLSpec = {
    "type": "function",
    "function": {
        "name": "query_metrics",
        "description": "查指标",
        "parameters": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]},
    },
}


class FakeModel:
    """scenario 决定每次 /chat/completions 怎么回。"""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.seen: list[dict] = []
        self.rounds = 0

    # -- 各场景的响应 -----------------------------------------------------------

    def respond(self, body: dict) -> tuple[int, dict]:
        self.seen.append(body)
        messages = body.get("messages", [])
        last = messages[-1] if messages else {}
        self.rounds += 1

        if self.scenario == "normal":
            # 第一轮发一个工具调用，第二轮给正文（模拟真实两轮）。
            if self.rounds == 1:
                return 200, self._choice(
                    reasoning=_THINK,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "query_metrics", "arguments": json.dumps({"start": "2026-06-01", "end": "2026-06-28"})},
                        }
                    ],
                )
            # 第二轮：校验 assistant 消息被整条回传（P13）。
            assistant = [m for m in messages if m.get("role") == "assistant"]
            assert assistant, "第二轮必须回传 assistant 消息"
            assert assistant[-1].get("reasoning_content") == _THINK, "reasoning_content 必须原样回传"
            tool_msg = [m for m in messages if m.get("role") == "tool"]
            assert tool_msg and tool_msg[-1].get("tool_call_id") == "call_1", "工具结果必须带 tool_call_id 回传"
            return 200, self._choice(reasoning=_THINK, content="六月经营数据已从数据库查询得出，明细见证据。[KB-001]")

        if self.scenario == "multi_tool_calls":
            # 一条 assistant 消息带两个工具调用（P7：每个都要回传）。
            if self.rounds == 1:
                return 200, self._choice(
                    reasoning=_THINK,
                    tool_calls=[
                        {"id": "call_a", "type": "function", "function": {"name": "query_metrics", "arguments": '{"start": "2026-06-01", "end": "2026-06-28"}'}},
                        {"id": "call_b", "type": "function", "function": {"name": "search_kb", "arguments": '{"query": "退款"}'}},
                    ],
                )
            tools = [m for m in messages if m.get("role") == "tool"]
            assert len(tools) == 2, "两个工具调用都要回传结果"
            return 200, self._choice(reasoning=_THINK, content="数据与文档都查到了。")

        if self.scenario == "empty_content":
            return 200, self._choice(reasoning=_THINK, content="")

        if self.scenario in ("length", "content_filter", "insufficient_system_resource", "aborted"):
            text = "回答写到一半" if self.scenario in ("content_filter", "aborted") else ""
            return 200, {"choices": [{"message": {"role": "assistant", "content": text, "reasoning_content": _THINK}, "finish_reason": self.scenario}]}

        if self.scenario == "bad_tool_args":
            if self.rounds <= 2:
                return 200, self._choice(
                    reasoning=_THINK,
                    tool_calls=[{"id": "call_x", "type": "function", "function": {"name": "query_metrics", "arguments": '{"start": "2026-06-0'}}],
                )
            return 200, self._choice(reasoning=_THINK, content="放弃工具。")

        if self.scenario == "hang":
            import time

            time.sleep(30)
            return 200, self._choice(reasoning=_THINK, content="太迟了")

        return 500, {"error": {"message": "boom", "type": "server_error", "code": None}}

    @staticmethod
    def _choice(reasoning: str, content: str = "", tool_calls: list | None = None) -> dict:
        message: dict = {"role": "assistant", "content": content, "reasoning_content": reasoning}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return {"choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}]}


def _make_handler(model: FakeModel):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            status, payload = model.respond(body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # 静音
            pass

    return Handler


@pytest.fixture()
def live_service():
    """起假模型 + 指向它的 live Service（短超时，hang 场景也要秒级返回）。"""

    def _make(scenario: str) -> tuple[Service, FakeModel]:
        model = FakeModel(scenario)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(model))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        settings = Settings(
            data_dir=_WORKSPACE / "data",
            kb_dir=_WORKSPACE / "knowledge_base",
            var_dir=_PROJECT / ".tmp-var",
            today=datetime.date(2026, 9, 1),
            llm_base_url="http://127.0.0.1:%d" % server.server_address[1],
            llm_api_key="test-key",
            llm_model="fake-model",
            llm_timeout=3.0,
            chat_budget=20.0,
        )
        svc = Service(settings)
        return svc, model, server

    return _make


# --------------------------------------------------------------------------- #
# 用例                                                                          #
# --------------------------------------------------------------------------- #


def test_live_tool_roundtrip(live_service):
    """正常两轮：模型查数 → 代码渲染，思考标记不得漏进对外字段。"""
    svc, model, server = live_service("normal")
    try:
        result = svc.chat("t1", "6月的净营业额是多少？")
    finally:
        server.shutdown()
    assert result["answer_type"] in ("data", "hybrid"), result
    assert result["answer"].strip()
    assert result["data_evidence"], "必须有工具证据"
    assert "RSN-test-marker" not in json.dumps(result, ensure_ascii=False)


def test_multiple_tool_calls_all_answered(live_service):
    """P7：一条消息两个工具调用，两个都要拿到 role=tool 结果。"""
    svc, model, server = live_service("multi_tool_calls")
    try:
        result = svc.chat("t2", "6月经营情况如何？")
    finally:
        server.shutdown()
    assert result["answer"].strip()
    assert len(model.seen) >= 2


@pytest.mark.parametrize("scenario", ["empty_content", "length", "content_filter", "insufficient_system_resource", "aborted", "http_500"])
def test_model_failure_becomes_refusal(live_service, scenario):
    """P9：空正文 / 异常 finish_reason / 错误码 → 结构化 refusal，answer 非空，半截话不外漏。"""
    svc, model, server = live_service(scenario)
    try:
        result = svc.chat("t3", "退款规则是什么？")
    finally:
        server.shutdown()
    assert result["answer_type"] == "refusal", result
    assert result["answer"].strip()
    assert "回答写到一半" not in result["answer"], "被掐断的半截正文不能当回答"
    assert "RSN-test-marker" not in result["answer"]


def test_bad_tool_args_recover_or_refuse(live_service):
    """arguments 是非法 JSON：不崩、不 500，最终要么恢复要么结构化 refusal。"""
    svc, model, server = live_service("bad_tool_args")
    try:
        result = svc.chat("t4", "6月营业额")
    finally:
        server.shutdown()
    assert result["answer"].strip()


def test_hang_returns_within_budget(live_service):
    """P11：模型不响应 → 单次超时生效，在预算内返回结构化 refusal。"""
    import time

    svc, model, server = live_service("hang")
    started = time.perf_counter()
    try:
        result = svc.chat("t5", "6月营业额")
    finally:
        server.shutdown()
    took = time.perf_counter() - started
    assert result["answer_type"] == "refusal"
    assert result["answer"].strip()
    assert took < 25, "hang 场景应在单次超时（3s）+重试余量内返回，实际 %.1fs" % took


def test_no_key_still_mock():
    """P12 对照面：没配 Key 时服务照常启动，chat 走 mock 引擎，不碰网络。"""
    settings = Settings(
        data_dir=_WORKSPACE / "data",
        kb_dir=_WORKSPACE / "knowledge_base",
        var_dir=_PROJECT / ".tmp-var",
        today=datetime.date(2026, 9, 1),
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        llm_timeout=3.0,
        chat_budget=20.0,
    )
    svc = Service(settings)
    assert svc.settings.llm_mode == "mock"
    result = svc.chat("t6", "6月的净营业额是多少？")
    assert result["answer_type"] == "data"
