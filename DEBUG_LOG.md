# DEBUG_LOG —— starter 缺陷根因分析

> 格式：现象 / 假设 / 验证 / 根因 / 修复 / 回归测试。
> 评测基线：接手时公开题库 **17 / 100**（`baseline_eval.txt`，commit `5ee86ed` 留存原始状态）。

---

## 缺陷 1：清洗模块完全空转，六条剔除规则一条都没执行

| 项 | 内容 |
|---|---|
| 现象 | `/api/health` 的 `valid_sales_rows` 与评测期望差 338 行；数据质量面板 removed 全 0 |
| 假设 | ① 数据本身没有脏行？② 清洗逻辑有 bug 把该删的留下了？ |
| 验证 | 直接查 `pos.db`：S99 外键 10 行、空日期/N/A 日期 8 行、DD-MM-YYYY 80 行、¥ 金额若干、空金额 150 行、qty≤0 30 行、完全重复行 100 行——脏数据确实存在且分布与 338 的缺口吻合（8+150+30+10+40+100=338） |
| 根因 | `starter/kbqa/cleaning.py` 的 `clean_rows`：docstring 自述"把 sales 原样搬过来"，日期照抄字符串、金额解析失败按 0、无规范化、无外键校验、无去重——与 KB-001 v3 §2/§3 的六条剔除规则一条都不对应 |
| 修复 | commit `1fc15a5`：新增 `parse_date`（三格式，DD-MM-YYYY 按日在前）；按 §3 顺序实现六条剔除；外键先去空白转大写再校验；七字段规范化后完全一致才判重复 |
| 回归测试 | `tests/test_batch1_cleaning.py` 12 用例（合成数据）：修复前 10 个 TypeError（旧签名不支持外键校验契约）+ `test_build_clean_db_report` 断言 kept=5≠1 红，修复后全绿 |

**取舍说明**：手册 §3.2 只定义了"空金额剔除"，非空但解析失败的金额（当前数据不存在）手册未定义——拍板为剔除并计入 `note_unparseable_amount`，不参与任何统计。

## 缺陷 2：区间右端开区间，单日查询恒为 0

| 项 | 内容 |
|---|---|
| 现象 | M04（S02/P06/2026-06-18 单日）期望净营业额 3625，服务返回 0；混合题 H02 同样症状 |
| 假设 | ① 数据里没有这天的记录？② 查询条件写错？ |
| 验证 | `SELECT COUNT(*) FROM sales WHERE store_id='S02' AND product_id='P06' AND date LIKE '%06-18'` → 53 行真实存在。对比 `_where` 生成的 SQL 发现 `date < end` |
| 根因 | `starter/kbqa/tools.py` `_where`：`date >= start AND date < end` 把右端点切掉。start==end 时区间为空。这是"先取整月再想当然"的典型错误——月度查询侥幸正确（下月 1 日前的数据都不含），一切单日/跨表对账查询全错 |
| 修复 | commit `0f56dd9`：改为 `date <= end`（KB-001 §4 按日期筛选的闭区间语义） |
| 回归测试 | `tests/test_batch1_metrics.py::test_closed_interval_single_day`：修复前返回 0 红，修复后 80.00 绿 |

## 缺陷 3：退款行被整体丢弃，净营业额/退款金额/销量/订单数全错

| 项 | 内容 |
|---|---|
| 现象 | M01 期望 `refund_amount=953.0`、`net_revenue=156757.0`，旧服务 refund 恒为 0 且 net 偏大 |
| 假设 | 退款金额字段是独立算的吧？是不是根本没实现？ |
| 验证 | 读 `query_metrics` SQL：`SELECT SUM(amount_cents), 0, COUNT(*), SUM(qty) ... AND is_refund=0`——第二列硬编码 0，退款行被 where 条件整体排除；net 因此包含了"少减的退款"，orders 把多行订单算多单 |
| 根因 | 同上，且这是口径级错误：KB-001 v3 §4 明确"退款行计入净营业额"（v3 相对 v2 的变更点 1），旧实现是 v2 的算法。交接文档说"月底数字跟财务对不上，是四舍五入"——实为口径错误，甩锅 |
| 修复 | commit `0f56dd9`：`query_metrics` 重写为 SUM 全部金额（退款负数自然相减）、退款金额取退款行合计绝对值、有效订单数 = 销售行不同 order_id 数、销量 = 销售行 qty − 退款行 qty、aov 无订单时 null |
| 回归测试 | `test_refund_counts_into_net`（修复前 net 140≠120、orders 3≠2 红）、`test_orders_count_distinct_order_id`（红）、`test_empty_period_zeros_and_null_aov` |

## 验证结果

- `pytest tests`：35 passed（starter 原有 17 + 批次 1 新增 18）。
- `python eval/run_eval.py --only metrics`：**6.00 / 6.00**（修复前该类仅 M05 得分）。
- M01 全字段与金标一致：net_revenue 156757.0 / refund_amount 953.0 / orders 4311 / aov 36.36 / qty 6496。
