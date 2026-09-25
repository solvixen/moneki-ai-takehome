"""批次 1 回归测试：清洗规则（KB-001 v3 §2/§3）。

这些测试在修复前必须是红的（证明缺陷存在），修复后转绿。
全部使用合成数据，不依赖作业包自带的 pos.db，评审换数据后依然有效。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kbqa.cleaning import build_clean_db, clean_rows


def make_source(tmp_path: Path, sales_rows) -> Path:
    """建一个最小的源库：2 家门店、2 个商品 + 指定的 sales 行。"""
    src = tmp_path / "src.db"
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
    conn.execute("INSERT INTO stores VALUES ('S02','店二','r','d')")
    conn.execute("INSERT INTO products VALUES ('P01','甲','c',10)")
    conn.execute("INSERT INTO products VALUES ('P02','乙','c',20)")
    conn.executemany("INSERT INTO sales VALUES (?,?,?,?,?,?,?)", sales_rows)
    conn.commit()
    conn.close()
    return src


def fetch_rows(src: Path):
    conn = sqlite3.connect(src)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT order_id, date, store_id, product_id, qty, amount, payment FROM sales"
    ).fetchall()
    conn.close()
    return rows


VALID_STORES = {"S01", "S02"}
VALID_PRODUCTS = {"P01", "P02"}


def make_rows(sales_rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE s (order_id TEXT, date TEXT, store_id TEXT, product_id TEXT,"
        " qty TEXT, amount TEXT, payment TEXT)"
    )
    conn.executemany("INSERT INTO s VALUES (?,?,?,?,?,?,?)", sales_rows)
    rows = conn.execute("SELECT * FROM s").fetchall()
    conn.close()
    return rows


def run_clean(sales_rows):
    return clean_rows(make_rows(sales_rows), VALID_STORES, VALID_PRODUCTS)


# -- §2.2 日期三格式 ----------------------------------------------------------


def test_date_iso_and_slash_normalized():
    rows, report = run_clean(
        [
            ("O1", "2026-06-18", "S01", "P01", "1", "10.00", "cash"),
            ("O2", "2026/6/19", "S01", "P01", "1", "10.00", "cash"),
        ]
    )
    assert report.removed["1_unparseable_date"] == 0
    dates = sorted(r[1] for r in rows)
    assert dates == ["2026-06-18", "2026-06-19"]


def test_dd_mm_yyyy_is_day_first():
    # KB-001 §2.2：25-07-2026 是 2026-07-25；07-06-2026 是 2026-06-07
    rows, _ = run_clean(
        [
            ("O1", "25-07-2026", "S01", "P01", "1", "10.00", "cash"),
            ("O2", "07-06-2026", "S01", "P01", "1", "10.00", "cash"),
        ]
    )
    dates = sorted(r[1] for r in rows)
    assert dates == ["2026-06-07", "2026-07-25"]


def test_unparseable_date_removed():
    _, report = run_clean(
        [
            ("O1", "N/A", "S01", "P01", "1", "10.00", "cash"),
            ("O2", "", "S01", "P01", "1", "10.00", "cash"),
        ]
    )
    assert report.removed["1_unparseable_date"] == 2
    assert report.kept_rows == 0


# -- §2.3 / §3.2 金额 ----------------------------------------------------------


def test_yen_amount_is_recoverable():
    rows, _ = run_clean([("O1", "2026-06-18", "S01", "P01", "1", "¥38.00", "cash")])
    assert len(rows) == 1 and rows[0][5] == 3800


def test_empty_amount_removed_not_backfilled():
    rows, report = run_clean(
        [("O1", "2026-06-18", "S01", "P01", "1", "", "cash")]
    )
    assert report.removed["2_empty_amount"] == 1 and rows == []


def test_negative_amount_is_refund_flag():
    rows, report = run_clean(
        [("O1", "2026-06-18", "S01", "P01", "1", "-20.00", "cash")]
    )
    assert rows[0][5] == -2000 and rows[0][7] == 1
    assert report.kept_refund_rows == 1


# -- §3.3 数量 -----------------------------------------------------------------


def test_qty_le_zero_removed():
    _, report = run_clean(
        [
            ("O1", "2026-06-18", "S01", "P01", "0", "10.00", "cash"),
            ("O2", "2026-06-18", "S01", "P01", "-2", "10.00", "cash"),
        ]
    )
    assert report.removed["3_qty_le_zero"] == 2


# -- §2.1 / §3.4 / §3.5 外键规范化 ---------------------------------------------


def test_store_id_normalized_before_fk_check():
    rows, report = run_clean(
        [
            ("O1", "2026-06-18", "s01 ", "P01", "1", "10.00", "cash"),
            ("O2", "2026-06-18", "S99", "P01", "1", "10.00", "cash"),
        ]
    )
    assert report.removed["4_store_not_in_stores"] == 1
    assert len(rows) == 1 and rows[0][2] == "S01"


def test_product_fk_removed():
    _, report = run_clean(
        [("O1", "2026-06-18", "S01", "P99", "1", "10.00", "cash")]
    )
    assert report.removed["5_product_not_in_products"] == 1


# -- §3.6 重复行 vs 合法多行订单 ------------------------------------------------


def test_exact_duplicate_kept_once():
    row = ("O1", "2026-06-18", "S01", "P01", "1", "10.00", "cash")
    rows, report = run_clean([row, row])
    assert len(rows) == 1
    assert report.removed["6_duplicate_row"] == 1


def test_multi_line_order_all_kept():
    rows, report = run_clean(
        [
            ("O1", "2026-06-18", "S01", "P01", "1", "10.00", "cash"),
            ("O1", "2026-06-18", "S01", "P02", "2", "20.00", "cash"),
        ]
    )
    assert len(rows) == 2 and report.removed["6_duplicate_row"] == 0


# -- 端到端：build_clean_db 的台账 ---------------------------------------------


def test_build_clean_db_report(tmp_path: Path):
    src = make_source(
        tmp_path,
        [
            ("O1", "2026-06-18", "S01", "P01", "2", "¥38.00", "cash"),
            ("O1", "2026-06-18", "S01", "P01", "2", "38.00", "cash"),  # 重复行
            ("O2", "N/A", "S01", "P01", "1", "10.00", "cash"),  # 坏日期
            ("O3", "2026-06-19", "S99", "P01", "1", "10.00", "cash"),  # 脏外键
            ("O4", "2026-06-19", "S01", "P01", "1", "", "cash"),  # 空金额
        ],
    )
    report = build_clean_db(src, tmp_path / "clean.db")
    assert report.raw_rows == 5
    assert report.kept_rows == 1
    assert report.kept_sales_rows == 1 and report.kept_refund_rows == 0
    assert report.removed["1_unparseable_date"] == 1
    assert report.removed["2_empty_amount"] == 1
    assert report.removed["4_store_not_in_stores"] == 1
    assert report.removed["6_duplicate_row"] == 1
