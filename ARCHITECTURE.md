# Shelwell 架构总览

> **这份文档只回答全局问题**：项目是什么、分成哪几层、边界在哪、接下来做什么。
> 具体接口、参数、实现方法和字段定义，放在各程序目录的 `README.md` 里。
> 文件索引见 [文件树.md](文件树.md)，跨模块规范见 [docs/README.md](docs/README.md)。

---

## 1. 项目定位

Shelwell（角色：**西尔维娅·希尔薇** / Sylvia Shelwell）是一个**带独立人格、长期记忆和关系演化**的中文 AI 伴侣，不是通用问答助手。

- **形态**：本地 27B 模型（Ollama）+ QQ 群聊/私聊 + Windows 桌面控制台
- **核心体验**：她记得你、对不同的人有不同态度、会主动接话，也会选择沉默
- **边界**：
  - 不做全能 Agent，不替用户操作电脑
  - 模型不负责“记不记得”“关系几级”这类确定性状态，状态由代码维护
  - 人格与关系是产品核心，不把回复简化成客服式问答
- **部署目标**：单机 Windows，Ollama、QQ 接入、控制台三部分可独立启停

## 2. 项目大纲

当前能力按“人格 → 记忆 → 关系 → 对话 → 群聊 → 控制台”展开：

| 能力 | 说明 | 状态 |
|---|---|---|
| 人格系统 | persona/facts yaml 编译为 system 提示词，支持切换 | 已有 |
| 长期记忆 | 情景 JSONL + 语义事实 + 向量/FTS 双索引 | 已有 |
| 关系演化 | 按直接互动与认识时长判定 5 级，唯一依赖对象 | 已有 |
| 对话编排 | 上下文构建、模型调用、清洗、切气泡、归档 | 已有 |
| 模型沉默 | 模型可输出 `[SILENT]`，QQ 只发旁白 | 已有 |
| QQ 群聊 | 触发词、回复引用、门禁、热度、多人归属 | 已有 |
| 群聊状态机 | 最小版 IDLE/WATCH/QUEUED/COOLDOWN，shadow 运行 | 已有 |
| 世界知识 | 设定 RAG，与“你与我”的记忆分离 | 已有 |
| 图片识别 | 表情包/小图识别，指纹缓存 | 已有（默认关闭） |
| 桌面控制台 | 服务、人格、记忆、关系、模型、调试 | 已有 |
| 语义自动巩固 | 每日提炼能力已实现，默认关闭 | 已有（默认关闭） |
| 离线生活模拟 | 闲置时在世界观内生成有因果闭环的生活日志（实验，不进入事实记忆） | 实验/规划 |
| 主动聊天 | 群聊主动 @ 某人 | 规划 |
| LoRA 挂载 | 人格模型切换 | 规划 |

## 3. 架构规划

### 3.1 五层结构

```text
L5 接入层     adapters/ + desktop/           QQ、控制台
L4 编排层     backend/orchestrator.py        一次对话的流程
L3 能力层     backend/ + backend/memory/     记忆、人格、关系、知识、视觉
L2 模型层     backend/llm/                   Ollama / OpenAI 兼容 API
L1 基础设施   config/ + 文件系统 + SQLite    配置、存储、日志
```

**依赖方向**：L5 → L4 → L3 → L2 → L1。下层不反向 import 上层；`backend/` 不 import `adapters/` 或 `desktop/`。

### 3.2 一次对话主链路

```text
QQ 消息 / 控制台请求
  → ContextManager 构建：system + 历史 + 动态记忆/关系/知识
  → Orchestrator 调 Provider
  → 回复清洗 + 气泡拆分 + 沉默判定
  → 归档到 JSONL / 更新关系
  → 返回 QQ / 控制台
```

关键点：

- **system 前缀冻结**：人格指令放独立 system；动态记忆/关系/知识下沉到本轮 user 尾部，避免每轮改 system 导致缓存失效
- **真相与索引分离**：JSONL 是权威，Chroma/FTS 只是可重建索引
- **状态与文本分离**：关系等级、是否沉默、门禁决策由代码判定，模型只执行

### 3.3 关键设计决策

1. **人格用 yaml 编译，不写死在代码**；代码只提供事实、关系和格式约束。
2. **记忆分“你与我”和“世界”**：私人情景/语义记忆与世界设定 RAG 使用独立 collection，避免混检索。
3. **关系等级双计数**：`direct_count` 决定等级，`msg_count` 只表示“群里见过”；避免群聊刷屏升级。
4. **群聊先门禁、后状态机**：`GroupGate` 当前是正式决策；`GroupStateMachine` 只 shadow 记录排队/合并/冷却，验证后再切 active。
5. **模型调试可追踪**：每次对话的完整 messages、原始输出、参数、错误落盘，供提示词迭代。
6. **回复格式交给代码**：模型只产出内容，清洗、字数、气泡、`[SILENT]` 由 `reply_format` / `orchestrator` 处理。

### 3.4 数据资产

| 数据 | 位置 | 作用 |
|---|---|---|
| 情景记忆 | `memory/history/*.jsonl` | 对话真相源，可编辑/删除 |
| 向量索引 | `memory/vector_store/chroma_db/` | 情景/语义/知识向量，可重建 |
| FTS 索引 | `memory/index/memory_fts.sqlite3` | 关键词召回，可重建 |
| 语义事实 | `memory/index/semantic.sqlite3` | 稳定事实，从情景提炼 |
| 关系等级 | `memory/index/relationships.sqlite3` | 5 级状态与直接互动计数 |
| 知识库 | `memory/vector_store/chroma_db/` collection `shelwell_knowledge` | 世界设定 RAG |
| 群聊 trace | `logs/group_gate.jsonl` / `logs/group_state.jsonl` | 门禁与状态机实验数据 |
| 模型 trace | `logs/llm_trace/` | 输入/输出/错误，提示词调试 |

## 4. 文档索引：从整体到细节

| 层级 | 文档 | 内容 |
|---|---|---|
| 全局 | 本文件 | 定位、分层、主链路、待办 |
| 索引 | [文件树.md](文件树.md) | 二级文件夹 + 每个文件的功能备注 |
| 后端总览 | [backend/README.md](backend/README.md) | L3/L4 模块、接口、数据流、约束 |
| 模型层 | [backend/llm/README.md](backend/llm/README.md) | Provider 接口、参数、错误、trace、usage |
| 记忆系统 | [backend/memory/README.md](backend/memory/README.md) | 归档/索引/语义/巩固/遗忘、数据路径 |
| 梗百科 | [backend/memewiki/README.md](backend/memewiki/README.md) | 梗匹配、注入、存储 |
| 接入层 | [adapters/README.md](adapters/README.md) | L5 规则 |
| QQ 接入 | [adapters/qq_bot/README.md](adapters/qq_bot/README.md) | OneBot、门禁、状态机、群聊配置 |
| 控制台 | [desktop/README.md](desktop/README.md) | 页面、services、worker、打包 |
| 配置 | [config/README.md](config/README.md) | app_config / runtime / persona / 知识 |
| 人格素材 | [config/persona_prompts/README.md](config/persona_prompts/README.md) | 人格、facts、知识源编辑与入库 |
| 脚本与测试 | [scripts/README.md](scripts/README.md) | 工具、测试清单、运行方式 |
| 跨模块文档 | [docs/README.md](docs/README.md) | 记忆设计、实现评审、规范、部署 |

**推荐阅读顺序**：本文件 → 文件树 → `backend/README.md` → 你正在改的模块 README → `docs/` 深文档。

## 5. 待办清单（摘要）

完整需求池见 [待办清单.txt](待办清单.txt)。当前优先级：

**P0：稳定性**
- 修复控制台 API 页上下文占用布局
- 模型预设名重新调研并更新
- 让 `test_*` 在 CI/本地一键跑全量

**P1：人格与记忆**
- 提示词继续瘦身，按“人格/事实/关系/格式”分层
- 语义记忆定时巩固（每日/空闲）
- 关系与状态机 shadow 数据复盘后，再决定是否切 active

**P2：功能扩展**
- 主动找人聊天（群聊 @ 某人）
- 离线生活模拟（实验）：闲置时生成有因果闭环的生活日志，先不进入事实记忆
- 插件规范（`docs/plugin-spec.md`）
- LoRA 挂载与人格切换
- 长期知识库入库流程产品化

## 6. 文档维护规则

- **本文件只写全局**：新增参数、接口、字段、实现细节，写到对应文件夹 `README.md`，不往这里堆。
- **新增/删除文件**：同步更新 [文件树.md](文件树.md) 的功能备注。
- **新增模块**：在模块根目录建 `README.md`；如果是新的一级/二级目录，同时更新文件树和本文件的文档索引。
- **跨模块设计**：放 `docs/`，由 `docs/README.md` 索引；模块 README 只链接，不复制。
- **过时内容**：优先删/改原文档，不在架构文档留“历史版本”。
