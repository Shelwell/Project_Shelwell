# backend/llm 说明

> L2 模型层：统一屏蔽 Ollama 与 OpenAI 兼容 API 的差异。
> 这里只写模型调用接口、参数、错误与调试；提示词拼接不在本层。

## 1. 文件

| 文件 | 职责 |
|---|---|
| `provider.py` | 统一推理入口，按 runtime 选择 ollama / openai_compatible |
| `trace.py` | 记录每次对话实际输入、原始输出、错误、耗时、用量 |
| `usage.py` | 进程内最近 200 次调用统计（不持久化） |

## 2. 调用接口

```python
from backend.llm import provider

text = provider.chat(
    messages,                 # [{"role": "system"/"user"/"assistant", "content": "..."}]
    keep_alive="10m",         # 可选；Ollama 模型驻留时间
    model_override=None,      # 可选；临时指定模型（如提炼小模型）
)
```

- 返回模型**原始文本**，不做清洗、不切气泡、不判断沉默
- 失败抛 `provider.ProviderError`
- `messages` 可带 `images`（base64 列表），仅 Ollama 视觉模型使用
- 模型选择：`runtime.active.provider` + `runtime.active.model`；`model_override` 优先级最高

## 3. Provider 选择

### Ollama

- 地址：`config/app_config.yaml` 的 `ollama.url`，默认 `http://localhost:11434`
- 端点：`POST /api/chat`，`stream=false`
- 请求：`model` / `messages` / `think` / `keep_alive` / `options`
- 超时：`ollama.chat_timeout`（默认 300s）
- 返回：`resp.json()["message"]["content"]`

### OpenAI 兼容

- 配置：`runtime.providers.openai_compatible`（`base_url` / `api_key` / `model` / `timeout` / `reasoning_effort`）
- 端点：`POST {base_url}/v1/chat/completions`
- 返回：`choices[0].message.content`
- 失败：`ProviderError`，错误信息带 HTTP 状态与前 200 字响应

## 4. 生成参数

优先级：`runtime.generation.*` → `app_config.generation.*`。

| 参数 | 默认 | 说明 |
|---|---|---|
| `temperature` | 0.7 | 采样温度 |
| `num_ctx` | 代码默认 8192；当前 `app_config`/runtime 为 32768 | 上下文窗口 |
| `num_predict` | 1024 | 最大生成 token |
| `keep_alive` | 代码默认 10m；当前 `app_config` 为 0.5h | Ollama 驻留；调用方可覆盖 |
| `think` | false | 推理模型的思考模式 |
| `top_p` | 未设置 | 仅显式配置时下发 |
| `repeat_penalty` | 未设置 | 仅显式配置时下发 |

## 5. 调试追踪（trace）

`orchestrator` 在调用前创建 `start_trace(prepared, meta)`，在返回/异常/沉默时 `finish()`。记录内容：

- `messages`：实际发给模型的完整 system、历史、动态块（图片 base64 换占位符）
- `raw_reply` / `cleaned_reply` / `bubbles` / `silent`
- `provider` / `model` / `params` / `duration_ms` / `usage`
- `error` / `error_detail`（含堆栈）
- `meta`：session、speaker、channel、query、上下文构建错误

落盘：

```text
logs/llm_trace/
├── index.jsonl
└── YYYY-MM-DD/HHMMSS_chat_<id>.json
```

配置：

```yaml
debug:
  trace_enabled: true
  trace_dir: logs/llm_trace
  trace_retention_days: 7
  trace_max_chars: 1000000
```

- `trace_enabled=false` 完全 no-op
- 超过 `trace_max_chars` 时截断中间历史，保留 system 与最后一条 user
- 控制台“调试”页读取 `index.jsonl` 展示，不需要手动翻文件

**注意**：trace 目前挂在 `Orchestrator.chat`，覆盖对话主链路；直接调用 `provider.chat` 的提炼/视觉调用不会自动记录。

## 6. 用量统计

```python
from backend.llm.usage import get_tracker

tracker = get_tracker()
tracker.last        # 最近一次调用
tracker.snapshot()  # 成功率、总 token、平均耗时
tracker.recent(20)  # 最近 N 次
```

- 进程内单例，重启清零
- 记录 `provider/model/ok/prompt_tokens/completion_tokens/elapsed/error`
- Ollama 的 `prompt_eval_count` / `eval_count` / `prompt_eval_duration` / `eval_duration` 会写入 usage，供 token 探针使用
- 控制台 API 页和模型页读取

## 7. 约束

- Provider 只做协议转换；不读人格、不拼记忆、不判断关系
- 不在本层做静默/清洗/截断；这些属于 `orchestrator` / `reply_format`
- 新增 provider 时：在 `chat()` 分发，补错误转换，并在本文件登记
- 修改 trace 字段时同步 `desktop/pages/trace_page.py` 的展示逻辑
