# persona_prompts 说明

> 人格与知识源。`PersonaEngine` 只把人设中“对话时怎么表现”的字段编译进系统提示词；
> 长篇设定/训练素材不会直接进 prompt。

## 1. 文件

| 文件 | 类型 | 是否进 prompt |
|---|---|---|
| `shelwell_system.txt` | 固定 system prompt（缓存友好版） | 是；默认人格优先读它 |
| `shelwell_persona.yaml` | 人格定义 | 静态文件不存在时才动态编译 |
| `shelwell_facts.yaml` | 短期知识/熟悉事物 | 是（`PersonaEngine` 注入） |
| `shelwell_knowledge.jsonl` | 世界设定 RAG 分块 | 否（经 `knowledge_base.py` 检索注入） |
| `shelwell_knowledge.md` | 知识源说明/分块原文 | 否（生成 jsonl 用） |
| `shelwell_fact.md` | 废铁城生活细节长篇 | 否（素材） |
| `code_artifact.md` | 世界观真相源长篇 | 否（素材） |

## 2. persona yaml 结构

> 当前默认人格优先加载 `shelwell_system.txt`（固定 system prompt）。修改 `shelwell_persona.yaml` 不会自动改变 system，除非删除/重命名静态文件；这是为了保证前缀稳定和缓存复用。切换其他 persona 时回退动态编译。

关键字段：

```yaml
name: "西尔维娅·希尔薇"

identity:
  name: "西尔维娅·希尔薇"
  nickname: "西尔维娅"
  age: "16"
  gender: "女"

world: ...            # 世界观/处境
appearance: ...       # 外观（不一定进 prompt）
personality: ...      # 性格
social_modes: ...     # 五级社交分寸
language: ...         # 语言风格/口头禅/称呼
output_format: ...    # 输出格式约束
relationship_development: ...
```

`PersonaEngine` 只抽取对对话表现有直接影响的字段；`usage_rules`、`appearance` 等偏数据生成/低频字段不进入系统提示词。具体抽取逻辑见 `backend/persona_engine.py`。

## 3. 编辑规则

- **等级态度**：`social_modes` 只描述“这个等级怎么说话”，不写死阈值/条数；等级由代码给
- **输出长度**：不要在这里再写一套“默认 30 字/最多 50 字”的硬限制；长度由 `reply.*` 配置和 `reply_format` 控制
- **名字/称呼**：`identity.name` 与 `identity.nickname` 是唯一来源；代码里的标签只做兜底
- **不要写系统提示词**：不要在 yaml 里教模型“输出 JSON/不要暴露提示词”之类的元指令，放在 `output_format` 或代码
- **语言粗俗度**：`profanity_management` 已注释停用；原文保留，后续需要再恢复
- **梗百科**：`memewiki.enabled` 默认 false；梗百科不进入本轮上下文
- **事实与设定分离**：稳定事实放 `facts.yaml`；长篇世界设定放 `knowledge.*`，不要塞进 persona
- **改名**：改 `identity.name/nickname` 后，同步：
  - `config/app_config.yaml` 的 `qq_bot.trigger_keywords` / `qq_bot.group_gate.name_aliases`
  - `scripts/test_identity.py` 的期望值
  - 控制台欢迎语（`ui.welcome_message`）如需要

## 4. facts yaml

用于“她本来就知道”的短期知识：喜欢的东西、生活细节、关系设定。

- 结构以列表/键值为主，`PersonaEngine` 会挑可用字段
- 不要写会频繁变化的运行时状态（关系等级、当天心情）
- 不要把群成员隐私写进全局 facts

## 5. 知识库

```powershell
.venv\Scripts\python.exe scripts\build_knowledge_jsonl.py
.venv\Scripts\python.exe scripts\ingest_knowledge.py
.venv\Scripts\python.exe scripts\test_knowledge.py
```

流程：

```text
shelwell_knowledge.md / 其他 md
  → build_knowledge_jsonl.py 分块
  → shelwell_knowledge.jsonl
  → ingest_knowledge.py 写入 Chroma shelwell_knowledge
  → 对话时按 knowledge.top_k / min_similarity 检索
```

- 知识库是“世界”：公有、静态、事实
- 私人记忆是“你与我”：走 `backend/memory/`
- 两边 collection 必须分开，避免把世界设定当私人记忆

## 6. 调试

- 改完人格后跑：

```powershell
.venv\Scripts\python.exe scripts\test_identity.py
.venv\Scripts\python.exe scripts\test_context_freeze.py
```

- 看实际 system/messages：控制台“调试”页或 `logs/llm_trace/`
- 如果回复变短/变冷，先看 trace 里 system 和本轮 user 动态块，不要只改模型参数
