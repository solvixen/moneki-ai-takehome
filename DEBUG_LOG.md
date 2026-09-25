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

## 缺陷 4（B1）：loader 只认 .md，txt/html 文档永远进不了索引

| 项 | 内容 |
|---|---|
| 现象 | 健康检查 `kb_docs=32`，知识库实际 35 篇；交接文档声称"md/txt/html 三格式都支持" |
| 假设 | 三篇非 md 文档（KB-022 txt / KB-061 html / KB-062 txt）被 loader 跳过了？ |
| 验证 | `loader.py:12` `SUPPORTED_SUFFIXES = {".md", ".markdown"}`；`load_document` 里 txt/html 的解析分支齐全但永远不被调用——死代码钓鱼 |
| 根因 | 后缀白名单缺 `.txt/.html/.htm`，rglob 扫到就被 `continue` 跳过；而 KB-062（营业时间调整，现行有效）恰是十几道题的金标 |
| 修复 | commit `643de1d`：白名单补三后缀 |
| 回归测试 | `tests/test_batch2_retrieval.py::test_loader_accepts_txt_and_html`（修复前红：只加载到 md） |

## 缺陷 5（B1）：GBK 编码按 UTF-8 硬解，中文变乱码

| 项 | 内容 |
|---|---|
| 现象 | GBK 编码文档（KB-062 旧 OA 导出）的中文全部乱码，正文生效日期认不出 |
| 假设 | `decode_bytes` 强制 `utf-8 errors=ignore`？ |
| 验证 | `loader.py:84` 确认；GBK 字节流按 UTF-8 忽略解码 → 中文成 mojibake，检索词对不上 + quote 逐字核对必挂 |
| 根因 | 单一硬编码编码，无探测。评测按原编码语义核对引用 |
| 修复 | commit `643de1d`：utf-8 → gb18030 依次试解，全失败才降级替换并写 warning（gb18030 是 GBK 超集） |
| 回归测试 | `test_gbk_txt_decoded_correctly`（修复前红：乱码 + effective_from=None） |

## 缺陷 6（B2）：分词按空白切，中文检索整体失效

| 项 | 内容 |
|---|---|
| 现象 | 评测报告：纯中文问题（退款/发票/台风/员工折扣）top5 全是同一组垫底文档；R 类几乎全灭 |
| 假设 | 查询词和索引词对不上？中文没被切开？ |
| 验证 | `tokenizer.py:22` `normalise(text).split()`：中文无空白边界，整句一个巨 token，与查询词永远不相交；合成实验确认"退款"查不到含"退款政策"的文档 |
| 根因 | 零分词。BM25 无从命中 |
| 修复 | commit `b3737c4`：零依赖二元组方案——ASCII 词整词保留，连续汉字段切 bigram（"退款政策"→退款/款政/政策），查询与索引两侧对称；TOKENIZER_VERSION 升级使缓存失效 |
| 回归测试 | `test_tokenize_chinese_produces_bigrams`、`test_tokenize_mixed_ascii_and_chinese`（修复前红）；修复后中文查询产生真实命中 |

**连锁效应**：分词修通后，`test_hit_doc_id_matches_its_own_chunk` 由"假绿"转红——此前命中全是 padded 片段，doc_id 错位路径未被触达。两个缺陷互相掩护。

## 缺陷 7（B3）：切块 range 右边界内缩，每篇文档丢尾巴

| 项 | 内容 |
|---|---|
| 现象 | 700 字文档切完只剩 600 字；文档末尾内容（附则/生效日期/联系方式）检索不到 |
| 假设 | `range(0, len-CHUNK_SIZE, CHUNK_SIZE)` 的右边界被内缩了一块？ |
| 验证 | `chunker.py:41` 确认；长度 ∈ (n·300, (n+1)·300] 时最后一段整段丢弃，600 字正好侥幸切完 |
| 根因 | 想当然的"防止最后一块越界"写法，实际把尾部整段删了。而文档结尾恰是引用核对最常命中的位置 |
| 修复 | commit `bc42550`：`range(0, len, CHUNK_SIZE)`，尾巴单独成块；CHUNKER_VERSION 升级使缓存失效 |
| 回归测试 | `test_chunker_keeps_tail`（修复前红） |

## 缺陷 8（B6）：元数据键名 state/status 对不上，版本路由从未生效

| 项 | 内容 |
|---|---|
| 现象 | "已废止且被取代"的文档照样出现在检索结果里；`filtered` 名单永远为空 |
| 假设 | 过滤逻辑本身没生效？键名对不上？ |
| 验证 | `loader.meta()` 输出 `"state": self.status`，而 `retriever._eligible` 与 `docfacts` 读 `meta.get("status")` → 恒 None；全库 grep 确认无任何 `"state"` 读取方 |
| 根因 | 键名不一致。"新旧版本并存、按生效日期路由"是总 README 明写的考点，这条使考点整体失效 |
| 修复 | commit `1322e57`：loader 统一输出 `"status"`；INDEX_VERSION 升级（缓存里存着旧键名的元数据，必须一并失效） |
| 回归测试 | `test_superseded_doc_is_filtered_out`（修复前红） |

## 缺陷 9（B5）：allowed 未排除已过滤文档，top_k 凑不满

| 项 | 内容 |
|---|---|
| 现象 | 已废止版本先占走 top_k 的坑，末端再被排除列表踢掉 → 返回条数 < top_k，违反契约 §4 |
| 假设 | 过滤发生在"补齐之后"而不是"打分之前"？ |
| 验证 | `retriever.search`：`allowed = set(range(len(chunks)))` 是全量集合 → 被过滤文档照样打分、占坑、补齐；`hits = [hit for hit in hits if hit.doc_id not in excluded]` 在末端补刀 |
| 根因 | 过滤时机错误：应在构造 allowed 时直接排除，从源头不占坑 |
| 修复 | commit `3b0311e`：allowed 按文档排除构造；末端补刀删除 |
| 回归测试 | `test_excluded_version_does_not_eat_top_k`（断言一依赖 B6 解锁；首版语料过薄会假红，已加厚） |

## 缺陷 10（B4）：命中片段的 doc_id 被其他文档覆盖（错位赋值）

| 项 | 内容 |
|---|---|
| 现象 | 检索结果里片段正文属于文档 A，doc_id 却是文档 B；引用逐字核对必挂（R10 里 KB-060 出现 3 次即此症状） |
| 假设 | `retriever.py:276` 那行 `hit.doc_id = ordered[len(hits)].doc_id` 是蓄意错位？ |
| 验证 | 单跑合成测试：同文档多块被 per-doc 限额跳过后，ordered（全量排序）与 hits（跳块后命中）错位，第 2 条命中的 doc_id 被改成排序序列里另一位选手的文档；单块文档恰好对齐，缺陷隐蔽 |
| 根因 | 蓄意写错的赋值行（前同事留的雷）；`hit` 本身的 doc_id/meta 本来就正确 |
| 修复 | commit `2c72c4f`：删除该行与无用的 `ordered`，doc_id/meta 一律来自片段自身 |
| 回归测试 | `test_hit_doc_id_matches_its_own_chunk`（加长合成文档使多块跳位可见；配合 B2 分词修复后由假绿转红，修复后绿） |

## 缺陷 11（B7）：conftest 类级永久 mock，毒杀整个测试会话

| 项 | 内容 |
|---|---|
| 现象 | 批次 2 真实检索测试单跑绿、跟 starter 测试一起跑就红，报错出现假数据 KB-013/FAKE_TEXT |
| 假设 | conftest 在某个夹具里永久改写了 `Retriever.search`？ |
| 验证 | `conftest.py:50` `retriever_module.Retriever.search = fake_search`：session 级夹具里改类属性、无恢复——第一个 API 测试一跑，全会话检索全被换成固定返回 |
| 根因 | "starter 测试全绿但答非所问"的完整机制：不仅自己测不了真东西，还杀死同会话所有真实测试 |
| 修复 | commit `69ff3f8`：client 降为函数级 + `monkeypatch.setattr` 打补丁自动恢复；API 测试行为不变 |
| 回归测试 | 全量 43 个测试绿（starter 17 + 批次 1 18 + 批次 2 8），批次 2 测试不再受测试顺序影响 |

## 验证结果

- `pytest tests`：43 passed（starter 原有 17 + 批次 1 新增 18 + 批次 2 新增 8）。
- `python eval/run_eval.py --only metrics`：**6.00 / 6.00**（修复前该类仅 M05 得分）。
- M01 全字段与金标一致：net_revenue 156757.0 / refund_amount 953.0 / orders 4311 / aov 36.36 / qty 6496。
