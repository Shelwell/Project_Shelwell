# config 说明

> 配置与人格素材。部署期配置和运行期配置分离；密钥只放 `runtime.yaml`，不入库。

## 1. 两个配置文件

| 文件 | 性质 | 谁改 | 是否入库 |
|---|---|---|---|
| `app_config.yaml` | 部署期默认配置 | 开发者/人工 | 是 |
| `runtime.example.yaml` | 运行期模板 | 开发者 | 是 |
| `runtime.yaml` | 本机运行期配置（含密钥） | 控制台/人工 | **否，gitignore** |

读取优先级：

```text
runtime.yaml 的显式值
  → app_config.yaml 的默认值
  → 代码内置默认值
```

- `backend/config_loader.py`：读 `app_config.yaml`，进程内缓存
- `backend/runtime_config.py`：读 `runtime.yaml`，按 mtime 热更新
- 控制台“模型/API/人格”页写 `runtime.yaml`

## 2. app_config.yaml

### 模型与推理

| 字段 | 默认 | 说明 |
|---|---|---|
| `model.base` | qwen3.8:27b | 基座模型 |
| `model.persona` | shelwell-persona | 挂 LoRA 后的人格模型名 |
| `model.embedding` | bge-m3 | 嵌入模型 |
| `ollama.url` | http://localhost:11434 | Ollama 地址 |
| `ollama.chat_timeout` | 300 | 对话超时秒 |
| `ollama.embedding_timeout` | 30 | 嵌入超时秒 |
| `generation.temperature` | 0.7 | 采样温度 |
| `generation.num_predict` | 1024 | 最大生成 token |
| `generation.num_ctx` | 32768 | 上下文窗口（当前固定 32k） |
| `generation.think` | false | 思考模式 |
| `generation.keep_alive` | 0.5h | 模型驻留 |
| `generation.top_p` / `repeat_penalty` | 未设置 | 仅显式配置时下发 |

### 上下文

| 字段 | 默认 | 说明 |
|---|---|---|
| `context.max_length` | 120 | 短期历史窗口（条） |
| `context.memory_enabled` | true | 是否检索长期记忆 |
| `context.compact_at` | 0.7 | 上次 prompt token 达到 num_ctx 的 70% 时压缩 |
| `context.keep_recent_turns` | 10 | 压缩后保留最近 10 轮原文 |
| `context.summary_model` | '' | 摘要模型；空=当前对话模型 |
| `context.summary_max_chars` | 800 | 摘要上限 |

### 记忆

| 字段 | 默认 | 说明 |
|---|---|---|
| `memory.top_k` | 3 | 情景召回条数 |
| `memory.min_similarity` | 0.5 | 情景相似度下限 |
| `memory.collection_name` | shelwell_memory | Chroma collection |

### 世界知识

| 字段 | 默认 | 说明 |
|---|---|---|
| `knowledge.enabled` | true | 是否启用知识 RAG |
| `knowledge.collection_name` | shelwell_knowledge | 独立 collection |
| `knowledge.top_k` | 2 | 单轮最多注入条数 |
| `knowledge.min_similarity` | 0.55 | 低于此值不注入 |
| `knowledge.path` | config/persona_prompts/shelwell_knowledge.jsonl | 知识源 |
| `memewiki.enabled` | false | 梗百科总开关；默认关闭，代码保留 |

### 关系

| 字段 | 默认 | 说明 |
|---|---|---|
| `relationship.enabled` | true | 是否启用关系等级 |
| `relationship.db_path` | memory/index/relationships.sqlite3 | SQLite |
| `thresholds.stranger_max_msgs` | 0 | 陌生直聊上限 |
| `thresholds.stranger_max_hours` | 24 | 24h 内仍陌生 |
| `thresholds.acquaint_max_msgs` | 300 | 认识到了解的直聊门槛 |
| `thresholds.acquaint_max_hours` | 120 | 5 天 |
| `thresholds.known_max_msgs` | 1000 | 了解到信任 |
| `thresholds.known_max_hours` | 360 | 15 天 |
| `thresholds.depend_min_msgs` | 3000 | 依赖直聊门槛 |
| `thresholds.depend_min_hours` | 1080 | 45 天 |

等级只看 `direct_count`；`msg_count` 只表示群里见过。依赖还要求 `runtime.relationship.dependent_id` 匹配。

### 回复格式

| 字段 | 默认 | 说明 |
|---|---|---|
| `reply.max_chars_per_bubble` | 40 | 单气泡上限 |
| `reply.max_bubbles` | 2 | 单轮最多气泡 |
| `reply.single_bubble_max_chars` | 45 | 不超过此长度强制合并一条 |
| `reply.silent_hint` | 括号旁白 | 模型沉默时代发 |

### 图片识别

| 字段 | 默认 | 说明 |
|---|---|---|
| `vision.enabled` | false | 是否启用 |
| `vision.model` | '' | 空=跟随对话模型 |
| `vision.max_side` | 1920 | 最长边上限 |
| `vision.max_bytes` | 2097152 | 文件上限 2MB |
| `vision.max_images` | 2 | 单条最多图 |
| `vision.max_per_minute` | 10 | 频率上限 |
| `vision.cache_path` | memory/index/vision_cache.sqlite3 | 指纹缓存 |

### 调试 trace

| 字段 | 默认 | 说明 |
|---|---|---|
| `debug.trace_enabled` | true | 是否记录模型输入/输出 |
| `debug.trace_dir` | logs/llm_trace | trace 目录 |
| `debug.trace_retention_days` | 7 | 保留天数 |
| `debug.trace_max_chars` | 1000000 | 单次输入截断上限 |

### QQ 群聊

| 字段 | 说明 |
|---|---|
| `qq_bot.group_enabled` | 是否处理群聊 |
| `qq_bot.trigger_keywords` | @/叫名字的触发词 |
| `qq_bot.auto_engage.*` | 热度、提问/话题/刷屏权重、半衰期 |
| `qq_bot.group_gate.*` | 门禁模式、阈值、活跃/静默时段、冷却、别名 |
| `qq_bot.group_state.*` | 状态机开关、模式、观察/聚合/冷却窗口、日志 |
| `qq_bot.group_context_limit` | 群共享上下文最多条数，默认 50 |
| `qq_bot.group_context_max_chars` | 群共享上下文字符上限，默认 6000 |

字段细节见 [../adapters/qq_bot/README.md](../adapters/qq_bot/README.md)。

### UI

`ui.page_title` / `page_icon` / `welcome_message`：控制台标题、图标、欢迎语。

## 3. runtime.yaml

| 字段 | 说明 |
|---|---|
| `active.persona` | 当前人格文件名（不带 `.yaml`） |
| `active.facts` | 当前 facts 文件名 |
| `active.lora` | LoRA 目录名，null=不挂载 |
| `active.provider` | `ollama` / `openai_compatible` |
| `active.model` | 当前对话模型 |
| `active.plugins` | 启用插件列表 |
| `generation.*` | 覆盖 app_config 的生成参数；null=用默认 |
| `providers.openai_compatible.*` | base_url/api_key/model/timeout/reasoning_effort/vision_enabled |
| `context.compress_enabled` | 是否压缩上下文 |
| `context.compress_threshold` | 超过多少条触发压缩 |
| `context.compress_keep_recent` | 压缩后保留最近几条 |
| `memory.extractor_model` | 语义提炼模型；空=跟随 active.model |
| `memory.auto_consolidate` | 每日自动提炼 |
| `memory.consolidate_time` | 自动提炼时间 HH:MM |
| `memory.consolidate_limit` | 单次最多处理条数 |
| `relationship.dependent_id` | 唯一依赖对象 QQ 号 |
| `relationship.dependent_name` | 控制台显示名 |

## 4. persona_prompts

> `config/scenarios/` 为空目录，预留给后续场景系统，当前不影响运行。

| 文件 | 用途 |
|---|---|
| `shelwell_persona.yaml` | 人格定义：身份、世界、社交分寸、语言、输出规则 |
| `shelwell_facts.yaml` | 她熟悉的事物/短期知识 |
| `shelwell_knowledge.jsonl` | 世界设定 RAG 分块（已入库） |
| `shelwell_knowledge.md` | 知识源说明（给人看/再生成 jsonl） |
| `shelwell_fact.md` | 废铁城生活细节长篇素材 |
| `code_artifact.md` | 世界观真相源长篇素材 |

人格/facts 的字段结构与编辑规则见 [persona_prompts/README.md](persona_prompts/README.md)。

> 粗俗度管理 `profanity_management` 已在 persona yaml 中注释停用；代码和原文保留，后续需要再恢复。

### 知识库入库

```powershell
.venv\Scripts\python.exe scripts\build_knowledge_jsonl.py
.venv\Scripts\python.exe scripts\ingest_knowledge.py
```

- `build_knowledge_jsonl.py`：Markdown → 分块 JSONL
- `ingest_knowledge.py`：写入 Chroma `shelwell_knowledge`
- 入库后可用 `scripts/test_knowledge.py` 验证召回

## 5. 安全与版本管理

- `runtime.yaml` 含 API key，已在 `.gitignore`
- 不要把 key 写进 `app_config.yaml`、代码、README、日志
- `memory/`、`logs/`、`dist/`、`data/`、`models/` 均为运行时/生成物，gitignore
- 改配置后：
  - `app_config.yaml` 需要重启进程（配置缓存）
  - `runtime.yaml` 按 mtime 热更新，控制台保存后通常立即生效
