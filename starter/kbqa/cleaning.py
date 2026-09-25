"""把原始 sales 导进 var/clean.db，指标都查这张表。清洗规则 = KB-001 v3 §2/§3。"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

#: 金额里的 `¥` 去掉再按数字解析。
_CURRENCY = str.maketrans("", "", "¥￥ \t　")

_DATE_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DATE_SLASH = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_DATE_DMY = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})")

REMOVAL_REASONS = (
    "1_unparseable_date",
    "2_empty_amount",
    "3_qty_le_zero",
    "4_store_not_in_stores",
    "5_product_not_in_products",
    "6_duplicate_row",
)


def parse_date(value: Optional[str]) -> Optional[str]:
    """KB-001 §2.2：接受 YYYY-MM-DD、YYYY/M/D、DD-MM-YYYY 三种格式。

    第三种是旧 POS 导出，**日在前月在后**：`07-06-2026` 是 2026-06-07。
    解析不了（含日历上不存在的日期）返回 None，由 §3.1 剔除。
    统一返回 ISO `YYYY-MM-DD`，后续查询全部基于规范形态。
    """
    text = (value or "").strip()
    m = _DATE_ISO.fullmatch(text) or _DATE_SLASH.fullmatch(text)
    if m:
        year, month, day = int(m[1]), int(m[2]), int(m[3])
    else:
        m = _DATE_DMY.fullmatch(text)
        if not m:
            return None
        day, month, year = int(m[1]), int(m[2]), int(m[3])
    try:
        return _date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_amount(value: Optional[str]) -> tuple[Optional[int], str]:
    """返回 (分, 状态)。状态取值：`ok`、`empty`、`bad`。

    KB-001 §2.3 与 §3.2：`¥38.00` 与 `38.00` 是同一个金额；空金额直接剔除，**不回填**。
    """
    text = (value or "").translate(_CURRENCY)
    if not text:
        return None, "empty"
    try:
        cents = int((Decimal(text) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None, "bad"
    return cents, "ok"


def parse_qty(value: Optional[str]) -> Optional[int]:
    """KB-001 §2.4：按整数解析。解析不了的按 0 处理，会被 §3.3 剔除。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class CleaningReport:
    raw_rows: int = 0
    kept_rows: int = 0
    kept_sales_rows: int = 0
    kept_refund_rows: int = 0
    removed: dict[str, int] = field(default_factory=lambda: {k: 0 for k in REMOVAL_REASONS})
    note_unparseable_amount: int = 0

    def as_dict(self) -> dict:
        return {
            "raw_rows": self.raw_rows,
            "removed": dict(self.removed, note_unparseable_amount=self.note_unparseable_amount),
            "kept_rows": self.kept_rows,
            "kept_sales_rows": self.kept_sales_rows,
            "kept_refund_rows": self.kept_refund_rows,
        }


def open_readonly(path: Path) -> sqlite3.Connection:
    """打开数据库。"""
    conn = sqlite3.connect(path.as_posix(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def clean_rows(
    rows: Iterable[sqlite3.Row],
    valid_stores: set[str],
    valid_products: set[str],
) -> tuple[list[tuple], CleaningReport]:
    """按 KB-001 v3 清洗：先规范化，再按 §3 顺序剔除，最后去重。

    - §2.1 门店/商品编号去空白转大写后再校验外键（顺序反了会误删真实订单）。
    - §2.3 `¥38.00` 与 `38.00` 同一个金额；带符号的行是退款，必须保留。
    - §3.2 空金额直接剔除，不回填。非空但解析不了的写法手册未定义，
      拍板：同样剔除，单独记在 note_unparseable_amount（README 有取舍说明）。
    - §3.6 只有七个字段规范化后完全一致的行才算重复；共用订单号的不同
      商品行是合法多行订单，全部保留。
    """
    report = CleaningReport()
    kept: list[tuple] = []
    seen: set[tuple] = set()
    for row in rows:
        report.raw_rows += 1
        date_iso = parse_date(row["date"])
        if date_iso is None:
            report.removed["1_unparseable_date"] += 1
            continue
        cents, status = parse_amount(row["amount"])
        if status == "empty":
            report.removed["2_empty_amount"] += 1
            continue
        if status == "bad":
            report.note_unparseable_amount += 1
            continue
        qty = parse_qty(row["qty"])
        if qty is None or qty <= 0:
            report.removed["3_qty_le_zero"] += 1
            continue
        store_id = (row["store_id"] or "").strip().upper()
        product_id = (row["product_id"] or "").strip().upper()
        if store_id not in valid_stores:
            report.removed["4_store_not_in_stores"] += 1
            continue
        if product_id not in valid_products:
            report.removed["5_product_not_in_products"] += 1
            continue
        order_id = (row["order_id"] or "").strip()
        payment = (row["payment"] or "").strip()
        record = (order_id, date_iso, store_id, product_id, qty, cents, payment)
        if record in seen:
            report.removed["6_duplicate_row"] += 1
            continue
        seen.add(record)
        kept.append(record + (1 if cents < 0 else 0,))
    report.kept_rows = len(kept)
    report.kept_refund_rows = sum(1 for row in kept if row[-1])
    report.kept_sales_rows = report.kept_rows - report.kept_refund_rows
    return kept, report


_SCHEMA = """
CREATE TABLE stores (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
CREATE TABLE products (product_id TEXT PRIMARY KEY, product_name TEXT,
                       product_category TEXT, unit_price REAL);
CREATE TABLE sales_clean (
    order_id TEXT, date TEXT, store_id TEXT, product_id TEXT,
    qty INTEGER, amount_cents INTEGER, payment TEXT, is_refund INTEGER
);
CREATE INDEX idx_clean_date ON sales_clean(date);
CREATE INDEX idx_clean_store ON sales_clean(store_id);
CREATE INDEX idx_clean_product ON sales_clean(product_id);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def build_clean_db(source: Path, target: Path) -> CleaningReport:
    """从只读的源库重建清洗表。返回清洗台账，供 `/api/health` 与数据质量面板使用。"""
    if not source.exists():
        raise FileNotFoundError("找不到源数据库：%s" % source)
    src = open_readonly(source)
    try:
        stores = [tuple(r) for r in src.execute("SELECT store_id, store_name, category, district FROM stores")]
        products = [
            tuple(r)
            for r in src.execute(
                "SELECT product_id, product_name, product_category, unit_price FROM products"
            )
        ]
        rows, report = clean_rows(
            src.execute("SELECT order_id, date, store_id, product_id, qty, amount, payment FROM sales"),
            {r[0] for r in stores},
            {r[0] for r in products},
        )
    finally:
        src.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    out = sqlite3.connect(target)
    try:
        out.executescript(_SCHEMA)
        out.executemany("INSERT INTO stores VALUES (?,?,?,?)", stores)
        out.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
        out.executemany("INSERT INTO sales_clean VALUES (?,?,?,?,?,?,?,?)", rows)
        out.execute(
            "INSERT INTO meta VALUES ('cleaning_report', ?)",
            (json.dumps(report.as_dict(), ensure_ascii=False),),
        )
        out.execute("INSERT INTO meta VALUES ('source_db', ?)", (source.name,))
        out.commit()
    finally:
        out.close()
    return report
