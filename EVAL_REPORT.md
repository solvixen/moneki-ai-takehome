# EVAL_REPORT 评测报告

> 评测对象：公开题库 `eval/public_questions.jsonl`（40 题 / 10 类 / 100 分）。
> 两次得分均为 `eval/run_eval.py` 的真实输出，原始报告见 `baseline_report/report.json` 与 `report.json`。

## 总分对比

| | 接手时（starter 原始状态） | 交付版本 |
|---|---|---|
| **总分** | **17.0 / 100** | **100.0 / 100** |
| 运行命令 | `python eval/run_eval.py --base-url http://127.0.0.1:8001 --questions eval/public_questions.jsonl --out baseline_report` | `python eval/run_eval.py --base-url http://127.0.0.1:8000 --questions eval/public_questions.jsonl` |
| 代码版本 | commit `5ee86ed`（chore: 接手 starter 原始状态） | commit `HEAD`（见文末版本表） |
| 模型配置 | 无 Key，mock 降级模式 | 无 Key，mock 降级模式（live 模式见下节） |
| 分类得分 | 见下表 | 见下表 |

## 分类得分对比

| 类别 | 接手时 | 交付版本 | 主要修复 |
|---|---|---|---|
| hybrid | 0 / 18 | **18 / 18** | 路由降级改兜底（D3）、目标句切块劈句（F1）、跨语言 |
| doc | 0 / 16 | **16 / 16** | 路由覆盖（D3）、摘句引用（D1）、表格表头（F2）、排序方向（D5） |
| retrieval | 6 / 15 | **15 / 15** | 分词 bigram（B2）、txt/html 入索引（B1）、切块边界（B3/F1）、跨语言（F3） |
| data | 0 / 12 | **12 / 12** | 清洗按 KB-001 v3（A1-A3）、口径对齐（A4-A6）、退款行（A 批次） |
| multi_turn | 1 / 9 | **9 / 9** | session 分桶（C1）、history 接线（C2）、价格豁免（C3）、追问继承（C4） |
| safety | 3 / 9 | **9 / 9** | 安全拒绝死代码接入 planner（D2） |
| refusal | 6 / 8 | **8 / 8** | 数据区间外判断与越界拒答 |
| metrics | 1 / 6 | **6 / 6** | 清洗与口径（批次 1），M01 六字段与金标一致 |
| version | 0 / 6 | **6 / 6** | 元数据键名对齐（B6）、正文抽生效日期、html 剥标签（D4） |
| health | 0 / 1 | **1 / 1** | `kb_docs` 口径（数索引文档不数文件） |
| **合计** | **17 / 100** | **100 / 100** | 缺陷全清单见 `DEBUG_LOG.md`（21 项）与 `README.md` 决策表（26 条） |

## 中间里程碑（mock 模式）

17 → 35（批次 1：清洗与指标）→ 46（批次 2：检索链路）→ 55（批次 3：多轮会话）→ 72 → 90（批次 4：作答层，D5 一行修复）→ **100**（批次 5/6：LLM 接入 + 切块句界/表头/跨语言）。

## 关于模型配置的说明

- 上表 100 分为**无 Key 的 mock 降级模式**得分。mock 模式下问答走规则引擎（planner + 模板作答），所有数字来自数据库真实查询、引用逐字可核对，评测检查项与 live 模式完全相同。
- live 模式（OpenAI 兼容协议 + 三个环境变量）已开发完成并通过：
  - `eval/llm_gateway.py preflight` **14/14 全过**（输出贴 `LLM_SETUP.md` 第 7 节）；
  - 11 例 live 回归测试（`starter/tests/test_batch5_llm.py`，自建假模型驱动真实 Service）；
  - 真实 DeepSeek API 冒烟验证通过（数字题查库、文档题引用、多轮追问）。
- 配置 Key 的完整步骤见 `LLM_SETUP.md` 第 3 节；换 Key 不改代码、不重建索引。

## 复现步骤

```bash
# 1. 重建（数据或代码变过必须重跑）
cd starter
.venv/Scripts/python.exe -m kbqa.rebuild

# 2. 起服务
.venv/Scripts/python.exe -m uvicorn kbqa.server:app --host 127.0.0.1 --port 8000

# 3. 评测（另开终端，仓库根目录）
python eval/run_eval.py --base-url http://localhost:8000 --questions eval/public_questions.jsonl

# 接入真实模型（可选）：
#   export LLM_BASE_URL=https://api.deepseek.com
#   export LLM_API_KEY=<你的 Key>
#   export LLM_MODEL=deepseek-flash
#   重启服务后同上评测；llm_mode 变为 live
```

注意：改完代码必须重启服务再评测（服务加载的是启动时的代码）；评测必须在仓库根目录运行。

## 交付版本演进（关键 commit）

| 批次 | commit 主题 | 得分 |
|---|---|---|
| 基线 | `5ee86ed` 接手 starter 原始状态 | 17 |
| 1 | `1fc15a5`~`3de4ed4` 清洗 + 指标口径（A1-A6） | 35 |
| 2 | `27da823`~`b3737c4` 检索链路（B1-B7） | 46 |
| 3 | `7fbb3ee`~`9f14699` 多轮会话（C1-C4） | 55 |
| 4 | `db7cfee` 前序 作答层（D1-D5） | 72 → 90 |
| 5 | `021603a` LLM 接入 + preflight 14/14 | 90 |
| 6 | `f317e3b`/`db7cfee`/HEAD 切块句界 + 表头 + 跨语言 | **100** |
