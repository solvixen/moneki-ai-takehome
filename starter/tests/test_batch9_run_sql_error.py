"""批次 9 回归测试：run_sql 猜错表名必须返回 error，而不是抛异常（缺陷 25）。

run_sql 是模型自己写 SQL 的出口，而它看不到数据库 schema——工具描述里一个字
都没提表名。模型只能凭常识猜，于是反复写出 sales / clean_sales / daily_metrics
这类不存在的表名。

猜错的后果两条，都比"答不出来"更糟：
1) tools.run_sql 的 conn.execute 直接抛 sqlite3.OperationalError，而 service.run_tool
   的 except 只接 (TypeError, ValueError) → 异常冒到 _answer 兜底，整题变 refusal；
2) 更隐蔽的是口径：就算表名猜对，模型自己写的 SQL 也不按 KB-001 v3 算
   （例如排除退款行，净营业额会差一个退款金额）。所以同时钉住"错误信息必须带
   真实可用的表名"，让它在第一次猜错后能自纠。

修复前预期：test_wrong_table_returns_error 红（抛异常）。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kbqa.config import Settings  # noqa: E402
from kbqa.service import Service  # noqa: E402

WORKSPACE = ROOT.parent

# 源库 data/pos.db 里真实存在的表名，但 clean 库里没有——模型最常猜错的就是它。
WRONG_TABLE_SQL = "SELECT count(*) FROM sales"


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


def test_wrong_table_returns_error(tmp_path):
    """猜错表名时工具必须返回 error，让模型看见并自己改对，而不是抛异常。"""
    svc = make_service(tmp_path)
    got = svc.run_tool("run_sql", {"sql": WRONG_TABLE_SQL})
    assert isinstance(got, dict)
    assert "error" in got, "猜错表名时抛了异常：模型看不到错误，这一轮就白跑了"


def test_error_carries_available_tables(tmp_path):
    """错误信息里要带真实表名，模型才能在下一轮自己改对。"""
    svc = make_service(tmp_path)
    got = svc.run_tool("run_sql", {"sql": WRONG_TABLE_SQL})
    assert "sales_clean" in got["error"], "没说清可用的表名，模型只能继续猜"


def test_correct_sql_still_works(tmp_path):
    """守卫：正确 SQL 不许被这次改动影响。"""
    svc = make_service(tmp_path)
    got = svc.run_tool("run_sql", {"sql": "SELECT count(*) AS n FROM sales_clean"})
    assert "error" not in got
    assert got["row_count"] == 1
