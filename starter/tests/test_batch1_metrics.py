"""批次 1 回归测试：指标口径（KB-001 v3 §4）。

修复前必须红：闭区间、退款计入净营业额、有效订单数去重、销量符号、
空区间 aov 为 null。合成数据，不依赖 pos.db。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kbqa.cleaning import build_clean_db
from kbqa.tools import DataTools

# O1 是一张合法的两行订单；R1 是退款行（金额为负，qty=1）
SALES = [
    ("O1", "2026-06-18", "S01", "P01", "2", "50.00", "cash"),
    ("O1", "2026-06-18", "S01", "P02", "1", "30.00", "cash"),
    ("O2", "2026-06-19", "S01", "P01", "3", "60.00", "cash"),
    ("R1", "2026-06-20", "S01", "P01", "1", "-20.00", "cash"),
]


@pytest.fixture(scope="module")
def tools(tmp_path_factory) -> DataTools:
    tmp = tmp_path_factory.mktemp("metrics")
    src = tmp / "src.db"
    conn = sqlite3.connect(src)
    conn.executescript(
        """
        CREATE TABLE stores (store_id TEXT PRIMARY KEY, store_name TEXT,
                             category TEXT, district TEXT);
        CREATE TABLE products (product_id TEXT PRIMARY KEY, product_name TEXT,
                               product_category TEXT, unit_price REAL);
        CREATE TABLE sales (order_id TEXT, date TEXT, store_id TEXT, product_id TEXT,
                            qty TEXT, amount TEXT, payment TEXT);
        """
    )
    conn.execute("INSERT INTO stores VALUES ('S01','店一','r','d')")
    conn.execute("INSERT INTO products VALUES ('P01','甲','c',10)")
    conn.execute("INSERT INTO products VALUES ('P02','乙','c',20)")
    conn.executemany("INSERT INTO sales VALUES (?,?,?,?,?,?,?)", SALES)
    conn.commit()
    conn.close()
    build_clean_db(src, tmp / "clean.db")
    return DataTools(tmp / "clean.db")


def test_closed_interval_single_day(tools: DataTools):
    # 修复前 date < end 把右端点切掉，单日查询恒为 0（M04 的根因）
    m = tools.query_metrics("2026-06-18", "2026-06-18")
    assert m["net_revenue"] == 80.00
    assert m["orders"] == 1
    assert m["qty"] == 3
    assert m["aov"] == 80.00


def test_refund_counts_into_net(tools: DataTools):
    # KB-001 §4：净营业额 = 销售行 + 退款行（退款为负）；退款金额 = |退款行合计|
    m = tools.query_metrics("2026-06-18", "2026-06-30")
    assert m["net_revenue"] == 120.00  # 50 + 30 + 60 - 20
    assert m["refund_amount"] == 20.00
    assert m["qty"] == 5  # O1 两行 2+1，O2 3，退款 −1
    assert m["aov"] == 60.00


def test_orders_count_distinct_order_id(tools: DataTools):
    # §4：有效订单数 = 销售行不同 order_id 数；两行订单算 1 单；退款行不计单
    m = tools.query_metrics("2026-06-18", "2026-06-30")
    assert m["orders"] == 2


def test_empty_period_zeros_and_null_aov(tools: DataTools):
    # M05：9 月无数据，全 0 且 aov 为 null（不是 0，也不能报错）
    m = tools.query_metrics("2026-09-01", "2026-09-30")
    assert m["net_revenue"] == 0.0
    assert m["orders"] == 0
    assert m["qty"] == 0
    assert m["aov"] is None


def test_daily_includes_empty_days_and_refund_day(tools: DataTools):
    days = tools.daily_metrics("2026-06-18", "2026-06-21")["days"]
    assert [d["date"] for d in days] == [
        "2026-06-18",
        "2026-06-19",
        "2026-06-20",
        "2026-06-21",
    ]
    by = {d["date"]: d for d in days}
    assert by["2026-06-18"]["net_revenue"] == 80.00 and by["2026-06-18"]["orders"] == 1
    assert by["2026-06-20"]["net_revenue"] == -20.00 and by["2026-06-20"]["orders"] == 0
    assert by["2026-06-21"]["net_revenue"] == 0.0 and by["2026-06-21"]["aov"] is None


def test_refund_attributed_to_refund_date(tools: DataTools):
    # §4：退款行按自己的日期归属。6/18 当天不应看到 6/20 的退款
    m = tools.query_metrics("2026-06-18", "2026-06-18")
    assert m["refund_amount"] == 0.0
