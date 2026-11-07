# backend 说明

> L3 能力层 + L4 编排层。这里只写后端内部接口与数据流；模型层、记忆层分别见
> [llm/README.md](llm/README.md)、[memory/README.md](memory/README.md)。

## 1. 分层与边界

```text
backend/orchestrator.py        L4 编排：一次对话的完整流程
backend/*.py                   L3 能力：上下文、人格、关系、知识、视觉、格式
backend/llm/                   L2 模型：provider / trace / usage
backend/memory/                L3 记忆：归档、索引、语义、巩固、遗忘
backend/memewiki/              梗百科
```

- `backend/` 不 import `adapters/` / `desktop/`，也不 import `PySide6`
- L3 模块不反向调用 `Orchestrator`
- 配置一律通过 `backend/config_loader.get()` 或 `runtime_config.get_runtime()` 读取
- 记忆的“真相”是 JSONL；向量/FTS/SQLite 都是可重建索引

## 2. 模块索引

| 文件 | 职责 | 主要接口 |
|---|---|---|
| `config_loader.py` | 读取 `config/app_config.yaml`，进程内缓存 | `get(path, default)` / `reload()` |
| `runtime_config.py` | 读取/写入 `config/runtime.yaml`，按 mtime 热更新 | `get_runtime()` / `save_runtime()` |
| `context_manager.py` | 构建 system + 历史 + 动态记忆/关系/知识；统一清洗 [YY] | `prepare_context(...)` / `archive_turn(...)` |
| `people.py` | QQ/别名 → 固定模型标识 [YY] | `PeopleRegistry.label()` / `scrub()` |
| `diary.py` | 第一人称日记存储 | `DiaryStore.append()` / `recent()` |
| `memory_retriever.py` | 记忆兼容层，委托 memory/service | `search(...)` / `search_semantic(...)` / `add_memory(...)` |
| `persona_engine.py` | 人格 yaml → 系统提示词 | `PersonaEngine().prompt` / `full_name` / `display_name` |
| `relationship.py` | 5 级关系判定、SQLite、回填、关系段渲染 | `RelationshipStore.observe/get/all/stats`、`render_relationship_block` |
| `group_policy.py` | 群聊门禁规则评分 | `GroupGate.decide(...)` / `GroupMessage` |
| `group_state.py` | 最小群聊状态机（shadow） | `GroupStateMachine.observe(...)` / `finalize(...)` |
| `knowledge_base.py` | 世界设定 RAG + JSONL/Chroma 增删改查 | `search(...)` / `list_items(...)` / `upsert_item(...)` / `delete_item(...)` / `rebuild_index(...)` |
| `vision.py` | 图片识别、缓存、限频 | `VisionPipeline.describe(...)` |
| `weather.py` | 持久化天气/温度/9 档时间 | `WeatherService.current()` / `summary()` / `environment()` |
| `reply_format.py` | 清洗、短回复合并、气泡拆分 | `split_bubbles(...)` / `strip_silent_marker` |
| `session_summary.py` | 日记/长期摘要（质量模型，token 探针触发） | `make_diary(...)` / `summarize_diaries(...)` |
| `orchestrator.py` | L4：建上下文 → 调模型 → 清洗 → 归档 | `Orchestrator.chat(...)` |

## 3. 核心接口

### 3.1 `Orchestrator.chat()`

```python
chat(messages, session_id="default", current_query=None,
     speaker_id=None, channel_type=None, channel_id=None,
     speaker_name="", speaker_hint="", quote_hint="",
     keep_alive=None, direct=True) -> dict
```

返回：

```python
{"reply": str, "bubbles": list[str], "ok": bool,
 "error": str|None, "silent": bool}
```

- `messages`：对话历史 `[{"role": "user"/"assistant", "content": "..."}]`
- `current_query`：本轮用户输入；为空时取 `messages` 最后一条 user
- `direct`：是否算直接互动；默认 `True`（本方法只处理真正要回复的轮次）
- 模型选择沉默时返回 `silent=True`，`reply` 是 `reply.silent_hint` 旁白
- 模型/上下文错误返回 `ok=False` + `error`，不抛异常给上层

### 3.2 `ContextManager.prepare_context()`

```python
prepare_context(messages, current_query=None, session_id=None,
                system_as_role=False, speaker_id=None,
                channel_type=None, channel_id=None,
                speaker_hint="", quote_hint="", speaker_name="",
                session_summary="") -> list
```

- `system_as_role=True` 时生成独立 system 消息；否则合并进首条 user
- system 前缀冻结：人格、固定规则放 system；动态记忆、关系、知识、时间下沉到本轮 user 尾部
- `session_summary` 是 token 探针触发后的早期对话摘要，动态注入，不写 system
- 天气摘要拼进 C1 摘要；当前日期/天气/温度/时间档放在 D 尾部
- 人物表 `[YY]` 统一由 `PeopleRegistry` 清洗；摘要/日记输入输出不保留已知昵称
- C1 由日记生成；C2 保留最近原文；群聊与私聊日记按 scope 分开
- 顺序：A system + B 群友档案 + C1 摘要/天气 → C2 历史 → D 当前发言人/环境 → E 当前句
- 群友档案按 TTL 冻结；群历史在首次进群时从归档生成 C1，并按估算 token 在接近上限时总结
- 默认人格优先加载 `config/persona_prompts/shelwell_system.txt` 作为固定 system；切换其他人格才回退动态编译
- 具体拼接顺序和来源过滤见 [memory/README.md](memory/README.md) 与 `docs/memory_design.md`

### 3.3 `ContextManager.archive_turn()`

```python
archive_turn(user_msg, ai_msg, session_id="default",
             speaker_id="", channel_type="", channel_id="",
             speaker_name="", direct=None)
```

- 内部调用 `MemoryService.add_memory()`，落 JSONL、提交索引任务、更新关系
- 归档同时写 `speaker_label`（`[YY]`）；`speaker_name` 保留原始昵称仅作审计
- 语义提炼输入/输出统一 `[YY]`：主语必须是当前发言人，宾语中的人物也带 `[YY]`
- `direct=None` 时：私聊算直接互动，群聊默认只算曝光
- 沉默回合归档时 `ai_msg=""`，但 `direct` 仍可为 `True`

### 3.4 关系

```python
RelationshipStore.observe(speaker_id, display_name="",
                          channel_type="", channel_id="",
                          at_me=False, reply_to_bot=False,
                          direct=False, ts=None)
render_relationship_block(rel, channel_type="private") -> str
```

- `direct_count` 决定等级；`msg_count` 只表示群里见过
- 阈值在 `config/app_config.yaml` 的 `relationship.thresholds`
- 唯一依赖对象存 `config/runtime.yaml`，等级由代码计算，模型无权升降
- 细节接口见 `backend/relationship.py` 文件头与 `scripts/test_relationship.py`

### 3.5 群聊门禁 / 状态机

```python
GroupGate.decide(group_id, sender_id, sender_name, text, ts,
                 at_me, at_other, is_question, reply_to_id,
                 reply_to_sender, bot_name, legacy_engage, msg_id)
GroupStateMachine.observe(group_id, ts, strong, weak,
                          gate_respond, gate_reason, score,
                          sender, text)
GroupStateMachine.finalize(group_id, ts)
```

- `GroupGate` 当前是正式决策（`qq_bot.group_gate.mode=active`）
- `GroupStateMachine` 默认 shadow，只写 `logs/group_state.jsonl`，不改变发送
- 具体状态、窗口、冷却见 [../adapters/qq_bot/README.md](../adapters/qq_bot/README.md)

### 3.6 回复格式

```python
split_bubbles(text, limit=40, max_bubbles=2, single_max=45) -> list[str]
is_silent_reply(text) -> bool
strip_silent_marker(text) -> str
```

- 模型只负责内容；字数、气泡、`[SILENT]` 清洗由代码完成
- 短回复优先合并成一条；超长按标点切分

## 4. 一次对话的数据流

```text
L5 传入 messages
  → ContextManager.prepare_context
      ├─ PersonaEngine → system
      ├─ 历史窗口
      ├─ MemoryRetriever → 相关情景/语义
      ├─ RelationshipStore → 关系段
      └─ KnowledgeBase → 世界知识
  → Provider.chat（L2）
  → Orchestrator 清洗/切气泡/沉默判定
  → Trace 记录完整输入输出
  → ContextManager.archive_turn
      ├─ JSONL 归档
      ├─ 索引任务入队
      └─ RelationshipStore.observe
```

## 5. 约束

- **不要绕过 Orchestrator 直接拼 prompt 调模型**；否则格式、沉默、归档、trace 会分叉
- **不要在前缀里放会变的动态内容**；会破坏缓存，也会让模型把旧关系当当前关系
- **关系/沉默/门禁是代码状态**；提示词只描述态度，不写死阈值
- **新增配置项**：先放 `config/app_config.yaml`，在 `config/README.md` 登记，再在代码里 `get()`
- **新增 L3 模块**：在本文件模块索引加一行；有独立子目录的，建子目录 `README.md`

## 6. 测试

```powershell
.venv\Scripts\python.exe scripts\test_identity.py
.venv\Scripts\python.exe scripts\test_context_freeze.py
.venv\Scripts\python.exe scripts\test_relationship.py
.venv\Scripts\python.exe scripts\test_silent.py
.venv\Scripts\python.exe scripts\test_reply_short.py
.venv\Scripts\python.exe scripts\test_memory_edit.py
```

完整清单与用途见 [../scripts/README.md](../scripts/README.md)。
