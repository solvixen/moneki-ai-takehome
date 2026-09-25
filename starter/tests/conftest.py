"""测试夹具。

API 层测试把检索换成固定返回，这样断言不跟着知识库内容变，跑起来也快。
要看真实检索效果直接起服务问两句，或写不依赖 `client` 的测试（批次 2 起，
检索层回归测试打真实索引）。

注意：补丁必须走 monkeypatch（函数级、测后自动恢复）。曾经这里直接
`Retriever.search = fake_search` 改类属性且永不恢复——会话里第一个 API
测试一跑，之后所有测试（包括别人的真实检索测试）都被投毒，这是
"测试全绿但答非所问"的另一层根因。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAKE_TEXT = "退款政策 v2 > 三、时限：外卖订单在订单送达后 24 小时内可以申请退款。"


@pytest.fixture(scope="session")
def var_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("var")
    os.environ["VAR_DIR"] = str(path)
    return path


@pytest.fixture
def client(var_dir, monkeypatch):
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)

    from fastapi.testclient import TestClient

    from kbqa import retriever as retriever_module
    from kbqa import server

    def fake_search(self, query, top_k=5, **kwargs):
        hit = retriever_module.Hit(
            doc_id="KB-013",
            chunk_id="KB-013#1",
            score=42.0,
            text=FAKE_TEXT,
            source_text=FAKE_TEXT,
            meta={"title": "退款政策 v2", "status": "现行"},
        )
        return retriever_module.SearchResult(
            hits=[hit][:top_k],
            query=query,
            terms=[],
            expansions=[],
            filtered=[],
            coverage=1.0,
        )

    # 函数级补丁：只影响请求了 `client` 的这个测试，测完自动恢复。
    monkeypatch.setattr(retriever_module.Retriever, "search", fake_search)
    return TestClient(server.app)
