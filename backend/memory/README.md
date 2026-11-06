# backend/memory 说明

> L3 记忆系统：情景记忆（发生过的对话）+ 语义事实（从对话提炼的稳定信息）。
> 设计细节与历史评审见：
> [docs/memory_design.md](../../docs/memory_design.md)、
> [docs/memory_implementation_review.md](../../docs/memory_implementation_review.md)。

## 1. 三层存储

```text
情景记忆（真相）
  memory/history/YYYY-MM-DD.jsonl
        │
        ├─ 索引（可重建）
        │    memory/vector_store/chroma_db/   向量
        │    memory/index/memory_fts.sqlite3  FTS5 关键词
        │
        └─ 提炼
             memory/index/semantic.sqlite3   语义事实
             memory/vector_store/chroma_db/   事实向量（另一 collection）
```

- **JSONL 是权威**：修改/删除先改 JSONL，再重建索引
- Chroma / FTS / 语义向量都是派生数据，丢了可以重建
- 关系库 `memory/index/relationships.sqlite3` 也由记忆归档驱动，但属于关系模块

## 2. 文件

| 文件 | 职责 |
|---|---|
| `models.py` | `MemoryEvent` / `SemanticFact` 数据模型、序列化 |
| `archive.py` | JSONL 追加/查询/更新/删除/统计 |
| `indexer.py` | Chroma 向量 + FTS5 双索引 |
| `embedder.py` | bge-m3 向量化（CPU，失败降级为纯关键词） |
| `jobs.py` | SQLite 索引任务队列，支持去重/重试/崩溃恢复 |
| `service.py` | 聚合入口：读写、检索、语义、群上下文、编辑、自动任务 |
| `semantic.py` | 语义事实 SQLite + 向量，按 owner 隔离 |
| `extractor.py` | 从同一人的多条情景中提炼事实（调模型） |
| `consolidator.py` | 情景 → 语义巩固，写事实并标记 `consolidated` |
| `forgetting.py` | 权重、命中回补、时间衰减、注入选择 |

## 3. 核心接口

### 3.1 写入

```python
MemoryService.add_memory(user_msg, ai_msg, session_id="default",
                         score=0.0, emotion="",
                         speaker_id="", channel_type="",
                         channel_id="", speaker_name="",
                         direct=None) -> bool
```

- 追加 JSONL；成功后再提交索引任务
- `direct=None`：私聊默认 `True`，群聊默认 `False`
- `direct` 只影响关系计数，不影响记忆内容
- 失败返回 `False`，不抛异常

### 3.2 检索情景

```python
MemoryService.search(query, top_k=None, min_similarity=None,
                     session_id=None, speaker_id=None,
                     channel_type=None, channel_id=None) -> list
```

返回：

```python
[{"content": str, "metadata": dict, "similarity": float,
  "source": "vector"|"fts"}]
```

- 默认 `top_k=memory.top_k`，`min_similarity=memory.min_similarity`
- 向量与关键词结果合并；阶段 A 是并集排序，后续可换 RRF
- 可按 `speaker_id` / `channel` 过滤，避免把别人的记忆注入当前说话人

### 3.3 语义事实

```python
MemoryService.search_semantic(query, top_k=5, min_similarity=0.4,
                              owner_id="") -> list
MemoryService.list_semantic_facts(page=1, page_size=20, ...) -> dict
MemoryService.consolidate(session_filter=None, limit=50, ...) -> dict
MemoryService.delete_semantic_fact(fact_id) -> bool
MemoryService.clear_semantic() -> int
```

- 语义事实按 `owner_id` 隔离（用户/角色）
- `consolidate()` 读取未巩固情景 → `Extractor` 提炼 → 去重/合并 → 写 `SemanticFact` → 标记情景 `consolidated=True`
- 已巩固的情景只能删除，不能修改（避免事实失去来源）
- 注入时 `format_injection` 会把旧昵称主语替换成“对方”，避免用户经历被模型当成自己的；昵称只做显示，归属以 `owner_id` 为准

### 3.4 群上下文

```python
MemoryService.get_group_context(channel_id, limit=10, bot_name="她") -> list
```

- 返回最近 N 条群消息：`[{"speaker","speaker_id","text","time","is_bot"}, ...]`
- 用于被触发时补充群上下文，由 `ContextManager` 格式化后注入
- `bot_name` 由调用方按当前人格传入，避免把她的旧名字标错
- 与“私人记忆”分开，群上下文只用于当前群的即时理解

### 3.5 编辑/删除

```python
MemoryService.update_event(event_id, user_msg=None, ai_msg=None) -> dict
MemoryService.delete_event(event_id) -> bool
MemoryService.delete_session(session_id) -> int
MemoryService.clear_all() -> dict
MemoryService.import_jsonl(path, session_override=None, ...) -> dict
```

- `update_event`：仅未巩固情景可改；重写 JSONL 后重建该条索引
- `delete_event`：删 JSONL + 索引 + 语义引用
- `delete_session`：按 session 批量删
- `clear_all`：清空情景/语义/索引，关系记录由调用方决定是否清
- `import_jsonl`：导入历史归档，按 `channel_type`/`ai_msg` 推断是否直接互动

## 4. 数据路径

| 数据 | 默认路径 |
|---|---|
| 情景 JSONL | `memory/history/` |
| Chroma 向量 | `memory/vector_store/chroma_db/` |
| FTS5 | `memory/index/memory_fts.sqlite3` |
| 索引任务队列 | `memory/index/jobs.sqlite3` |
| 语义事实 | `memory/index/semantic.sqlite3` |
| 语义向量 | 同一 `memory/vector_store/chroma_db/`，collection `shelwell_semantic` |
| 自动巩固标记 | `memory/index/last_auto_consolidate.txt` |
| 关系库 | `memory/index/relationships.sqlite3` |

测试时可通过 `MemoryService(archive_dir=..., chroma_dir=..., fts_db=..., jobs_db=...)` 指向临时目录。

## 5. 配置

```yaml
memory:
  top_k: 3
  min_similarity: 0.5
  collection_name: shelwell_memory
```

- `memory.top_k`：情景召回条数
- `memory.min_similarity`：情景相似度下限
- `memory.collection_name`：Chroma collection 名
- 嵌入模型：`model.embedding`（默认 bge-m3）
- 语义检索默认 `top_k=5`、`min_similarity=0.4`，由调用方覆盖

## 6. 索引与恢复

- 写入 JSONL 后由 `jobs.enqueue("index", {"event_id": ...})` 异步入队
- `process_index_job` 调 `Indexer.index()`：向量 + FTS 双写
- 进程启动时 `jobs.recover_stale()` 恢复中断任务
- 索引可随时重建：

```powershell
.venv\Scripts\python.exe scripts\rebuild_index.py
```

- 如果嵌入服务不可用：`embed_safe` 返回空向量，FTS 仍可检索，系统降级不崩

## 7. 巩固与遗忘

- **巩固**：`Extractor` 从同一人的多条情景中提炼；“我喜欢羽毛球”这类稳定事实进入语义层
- **自动巩固**：`MemoryService` 可启动后台 worker，按配置每日跑一次；当前默认手动/低频
- **遗忘**：`forgetting.py` 用基础分 + 命中回补 - 时间衰减排序，只影响注入选择，不删除数据
- **编辑限制**：`consolidated=True` 的情景只读，防止事实来源被改

## 8. 约束

- 不把群聊旁观当私人记忆注入；归属靠 `speaker_id` / `channel_id`
- 不把世界知识写进私人记忆 collection；知识库走 `knowledge_base.py`
- 不直接改 Chroma/SQLite 当真相；先改 JSONL
- 新增字段：先改 `MemoryEvent` / `SemanticFact` 与 `from_dict/to_dict`，再考虑兼容旧归档
- 关系计数逻辑不要复制到本模块；统一走 `MemoryService.add_memory(direct=...)`

## 9. 测试

```powershell
.venv\Scripts\python.exe scripts\test_memory_retrieval.py
.venv\Scripts\python.exe scripts\test_memory_edit.py
.venv\Scripts\python.exe scripts\test_memory_delete.py
.venv\Scripts\python.exe scripts\test_auto_consolidate.py
.venv\Scripts\python.exe scripts\test_extractor_identity.py
```

完整清单见 [../../scripts/README.md](../../scripts/README.md)。
