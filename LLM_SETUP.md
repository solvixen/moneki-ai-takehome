# LLM 接入说明

## 1. 用了什么

- **协议**：OpenAI 兼容 Chat Completions（`POST {LLM_BASE_URL}/chat/completions`）。
- **厂商/模型**：开发联调时用的是作业包自带的假模型（`eval/llm_gateway.py`）；评审时换成 DeepSeek `deepseek-flash` 即可，代码不做任何改动。
- **SDK**：不用 OpenAI SDK，客户端是手写的（`starter/kbqa/llm.py`，约 200 行），依赖仅 `httpx>=0.28.1`。手写的原因：地址原样拼接、错误分类、重试策略、trace 留痕都要自己控制，手写反而比 SDK 少一层黑盒。
- **调用方式**：非流式。一次 `/api/chat` 内最多 4 轮工具调用（`live.py` 的 `MAX_TOOL_ROUNDS=4`）。

## 2. 配置从哪里读

全部来自环境变量，启动时读取一次（`starter/kbqa/config.py`）：

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `LLM_BASE_URL` | 是 | 空 | 模型服务地址。**原样拼接** `/chat/completions`，不补 `/v1`、不截路径、不改写 |
| `LLM_API_KEY` | 是 | 空 | 以 `Authorization: Bearer <key>` 发送 |
| `LLM_MODEL` | 是 | 空 | 模型名，原样放进请求的 `model` 字段 |
| `LLM_TIMEOUT` | 否 | `120` | 单次模型调用超时秒数（契约要求不小于 120） |
| `CHAT_BUDGET` | 否 | `150` | `/api/chat` 整体时间预算秒数（契约要求 180 秒内返回） |

三个必填项**任一为空即进入 mock 降级模式**（`llm_mode: "mock"`），没有第三种状态。启动时不校验 Key 格式、不调用列模型/查余额等任何接口。

## 3. 怎么换成你们的

只改环境变量，**不改代码、不重建索引**，重启服务即可：

```bash
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY=<你们的 Key>
export LLM_MODEL=deepseek-flash

cd 候选人作业包/starter
.venv/Scripts/python.exe -m uvicorn kbqa.server:app --host 127.0.0.1 --port 8000
# Linux/macOS: python3 -m uvicorn kbqa.server:app --host 0.0.0.0 --port 8000
```

验证：`curl http://localhost:8000/api/health` 应显示 `"llm_mode": "live"`。如果仍是 `mock`，说明三个变量有一个没传进服务进程。

## 4. 怎么看到发给模型的请求

两种方式：

**方式一（推荐）：作业包代理**。请求会原样经过它并逐条记 JSONL：

```bash
python3 eval/llm_gateway.py proxy --upstream https://api.deepseek.com --log llm_traffic.jsonl
# 然后用它打印的地址作为 LLM_BASE_URL 启动服务
```

**方式二：内置 trace**。每次问答的完整提示词预览（前 4000 字）、模型原始输出、工具往返都记在 trace 里：

```bash
curl http://localhost:8000/api/trace/<trace_id>
# trace.steps 里的 llm 记录：prompt（发给模型的 messages 预览）、
# raw_content / raw_reasoning（模型原文）、tool_calls、usage、耗时
```

`Authorization` 值不会写入任何日志。

## 5. 没有 Key 时会怎样

- 服务**正常启动**，`/api/health` 报 `"llm_mode": "mock"`，其余字段照常。
- `/api/metrics/*`、`/api/retrieve` 完全不受影响（本来就不碰模型）。
- `/api/chat` 走内置规则引擎（planner + 模板作答）：取数、检索、多轮追问、安全拒绝全部可用，返回结构完整，**不会 HTTP 500**。
- 系统提示词要求所有数字必须来自工具结果，模型口误的数字会被代码校验并回退到模板作答（见第 8 节）。

## 6. 依赖与安装

- 唯一新增依赖：`httpx>=0.28.1`（`pyproject.toml` 已声明，`uv sync` / `pip install -e .` 安装）。
- 无模型文件下载、无向量模型、无本地算力要求，首次启动秒级（索引缓存已随仓库提交）。

## 7. 自测结果

`python3 eval/llm_gateway.py preflight --service-url http://127.0.0.1:8000 --no-wait`，**14 项全部通过**：

```
P1    服务确实把请求发到了注入的 LLM_BASE_URL（含路径前缀）             通过  共观察到 60 次 POST /ds-gw/chat/completions。
P2    请求里的 model 等于注入的 LLM_MODEL                               通过  全部请求都用了 preflight-model-7f3a。
P3    注入的 Key 以 Authorization: Bearer 发送                           通过  全部请求都带了正确的 Bearer Key。
P4    只用了 DeepSeek 文档列出的顶层参数                                通过  只出现了 DeepSeek 文档列出的顶层参数。
P5    max_tokens 不设，或不小于 2048                                    通过  max_tokens 都不小于 2048。
P6    没有访问 {prefix}/chat/completions 之外的任何路径                 通过  只访问了 POST /ds-gw/chat/completions，没有碰任何别的路径。
P7    工具定义规范，且每一个工具调用都以 role=tool + tool_call_id 回传  通过  工具定义规范，44 个工具调用的结果都正确回传了。
P8    每个场景下 /api/chat 都返回 HTTP 200 与字段完整的合法 JSON        通过  32 次问答全部返回 200 和字段完整的 JSON。
P9    模型不可用时给出结构化 refusal，answer 从不是空串                 通过  模型不可用的场景下都给了结构化 refusal 或有据可查的回答，answer 从不是空串。
P10   思考内容没有漏进 answer / citations / data_evidence               通过  32 次回答里，思考标记都没有出现在任何对外字段里。
P11   /api/chat 在时限内返回（含长时间无响应的场景）                    通过  最慢的一次是 120.05 秒，都在 180 秒以内。
P12   注入环境变量后 /api/health 报告 llm_mode = live                   通过  llm_mode = live。
P13   多轮工具调用之间 reasoning_content 原样回传（没有触发 400）       通过  18 次多轮请求都原样回传了 reasoning_content。
P14   保持连接的空行与 SSE 注释没有把服务弄坏                           通过  正文前的空行和 SSE 的 `: keep-alive` 注释都被正确跳过了，slow 场景照常给出回答。

预检通过：在 OpenAI 兼容这条路线上，我们能原样接上你的服务。
```

契约 7.3 各条的处理位置（均在 `starter/kbqa/llm.py` 与 `live.py`）：

- **思考模式**：不关闭（理由见 README），回答只取 `message.content`，`reasoning_content` 只留 trace。
- **reasoning_content 回传**：assistant 消息**整条追加**进 messages，不挑字段重组（`live.py` D8 注释处）。
- **max_tokens**：固定 4096（≥2048）；`finish_reason=length` 或无 tool_calls 而正文为空 → 抛 `LLMError` → 结构化 refusal。
- **异常 finish_reason**：`length` / `content_filter` / `insufficient_system_resource` / `aborted` 一律按错误处理，半截正文不进 answer。
- **工具调用**：`arguments` 是 JSON 字符串，`json.loads` 解析失败会把错误以 `role: "tool"` 回传给模型让它重试；连续 2 轮解析失败才放弃；多工具调用逐个回传。
- **保持连接**：非流式，响应体先 `strip()` 再解析，空行无影响。
- **错误码**：400/401/402/422/500/503 → `LLMError(http_error)` → 结构化 refusal，真实原因写 trace；429/500/503 且预算充足时**重试一次**。
- **超时预算**：单次调用超时 = min(120, 剩余预算)；剩余预算不足 10 秒即放弃，返回 refusal，绝不挂起。
- **文档外参数**：请求体只有 `model` / `messages` / `max_tokens` / `tools` / `tool_choice`，没有 `seed`、`parallel_tool_calls`、`n` 等文档外参数。

另外有一套 11 例的 live 回归测试（`starter/tests/test_batch5_llm.py`）：自建假模型驱动真实 Service，覆盖正常两轮、双工具调用、六种失败场景、hang 超时、无 Key 降级，76 个测试全绿。

## 8. 已知限制

1. **非流式输出**：未实现 SSE 流式，`/api/chat` 等全部轮次完成后一次性返回。慢问题（思考模式 + 多轮工具）最坏可到两分钟，前端目前只能干等。契约允许非流式，但体验上有代价。
2. **数字校验的保守回退**：模型正文里出现工具结果对不上的数字时，整条回答回退到 mock 模板作答（而不是让模型重写）。极少数情况下模型做简单换算（如"增长约 3%"里的 3）也可能触发回退——回答永远有据，但偶尔不如模型原话流畅。
3. **重试策略简单**：只对 429/500/503/空正文/网络异常重试一次，无指数退避；401/402/422 这类确定性错误不重试，直接 refusal。
4. **多轮对话只带最近 3 轮历史**给模型，更早的上下文不进提示词（planner 的追问还原独立工作，不受影响）。
5. **未与真实 DeepSeek 接口对照过**：开发时无 Key，全部验证基于作业包假模型与官方文档。假服务 README 列出的几处"推定行为"（400 正文结构、422 取值等）如与真实接口有出入，请以真实表现为准。
6. **httpx `trust_env=False`**：客户端不读 `HTTP_PROXY` 等环境代理变量。评审网络如需经企业代理访问 DeepSeek，请直接把代理地址写进 `LLM_BASE_URL`（例如作业包 proxy 模式的地址），这本身就是推荐做法。
