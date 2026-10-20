# Shelwell 项目架构

> 本文档是 Shelwell 的**唯一架构真相源**。Agent 和开发者修改代码前必读。
> 任何新功能必须按本文档的分层规则嵌入，不得随意新增顶层目录或破坏既有边界。

---

## 1. 项目定位

Shelwell 是一个**带独立人格和长期记忆的中文 AI 伴侣**。

核心主张：
- **人格来自权重，不是提示词**：通过 LoRA 微调把性格写进模型，而非靠 system prompt 伪装。
- **记忆分层**：短期上下文 + 长期向量记忆 + 对话归档，三级优先级。
- **多端接入**：同一套后端，可接 Web UI、QQ 等任意前端。

当前阶段：第三阶段（人格 LoRA 插件）进行中。训练集已备，本地 12B 训练受显存限制，训练方案待定。

---

## 2. 现有目录盘点
E:\Shelwell├── .venv/                    # 运行环境（Python 3.12.7，唯一运行环境）├── app/                      # 前端（Streamlit Web UI）│   ├── main.py│   ├── pages/│   └── assets/├── backend/                  # 后端核心│   ├── config_loader.py│   ├── context_manager.py│   ├── memory_retriever.py│   └── init.py├── config/                   # 配置│   ├── app_config.yaml│   ├── persona_prompts/shelwell_persona.yaml│   └── scenarios/├── data/                     # 数据（git 忽略）│   └── persona_lora/         # 人格训练集├── scripts/                  # 工具脚本│   ├── data_prep/│   └── generate_file_tree.py├── models/                   # 模型权重（git 忽略）│   ├── base_model/rudy-nemo-12b-v1/│   └── lora_adapters/├── memory/                   # 运行时数据（git 忽略）├── logs/                     # 日志（git 忽略）├── ARCHITECTURE.md           # 本文件├── requirements.txt          # 运行依赖（已锁版本）├── requirements-train.txt    # 训练环境快照（记录用，非安装依据）├── start.bat                 # 启动器（激活 .venv）├── 文件树.md└── 大纲.txt外部资源（不在项目内）：E:\Tools\unsloth\                    # Unsloth 源码（第三方）C:\Users....unsloth\studio\        # Unsloth 训练环境（官方安装器管理）E:\Shelwell\Ollama_models\           # Ollama 模型库（git 忽略）text复制下载
## 3. 目标架构（分层）

新功能按以下五层归位。**跨层调用只能自上而下，禁止反向依赖。**
┌─────────────────────────────────────────────┐│  L5 接入层  adapters/                        │  前端/协议适配│  Web UI (app/) · QQ Bot · CLI · API Server   │├─────────────────────────────────────────────┤│  L4 编排层  backend/orchestrator.py          │  一次对话的完整流程│  收消息 → 建上下文 → 调模型 → 归档 → 回消息    │├─────────────────────────────────────────────┤│  L3 能力层  backend/                         │  可复用的能力模块│  context_manager · memory_retriever          ││  persona_engine · vision_pipeline(未来)      ││  sticker_manager(未来)                        │├─────────────────────────────────────────────┤│  L2 模型层  backend/llm/                     │  推理后端封装│  ollama_client · lora_loader · embedder      │├─────────────────────────────────────────────┤│  L1 基础设施  config/ · memory/ · logs/       │  配置/存储/日志│  config_loader · storage · logger            │└─────────────────────────────────────────────┘text复制下载
**各层职责**：

| 层 | 职责 | 禁止 |
|---|---|---|
| L5 接入 | 协议转换、消息收发、UI 渲染 | 不写业务逻辑，不直接调 L2/L1 |
| L4 编排 | 串起一次对话的完整流程 | 不实现具体能力，只做调度 |
| L3 能力 | 上下文、记忆、人格、视觉、表情 | 不关心消息从哪来、往哪发 |
| L2 模型 | 封装 Ollama/Unsloth/嵌入模型调用 | 不处理业务语义 |
| L1 基础设施 | 配置、存储、日志、路径 | 不含任何业务逻辑 |

---
## 4. 模块职责细则

### L1 基础设施

| 模块 | 路径 | 职责 | 接口 |
|---|---|---|---|
| 配置加载 | `backend/config_loader.py` | 加载 `config/app_config.yaml`，单例缓存 | `load_config(reload)`, `get(path, default)` |
| 配置本体 | `config/app_config.yaml` | 模型/推理/记忆/UI 全部可调参数 | — |
| 日志 | `backend/logger.py`（待建） | 统一日志，分级输出到 `logs/` | `get_logger(name)` |

**规则**：所有硬编码的参数必须迁到 `app_config.yaml`，通过 `get("a.b.c")` 读取。新增配置项必须同步更新本文档第 6 节。

### L2 模型层

| 模块 | 路径 | 职责 |
|---|---|---|
| Ollama 客户端 | `backend/llm/ollama_client.py`（待建） | 封装 `/api/chat`、`/api/embeddings`，统一超时和重试 |
| LoRA 管理 | `backend/llm/lora_loader.py`（待建） | 挂载/卸载 LoRA，人格切换 |
| 嵌入器 | `backend/llm/embedder.py`（待建） | 文本向量化，供记忆模块调用 |

**规则**：L3 及以上**禁止直接 `import requests` 调 Ollama**，必须走 L2 封装。当前 `memory_retriever.py` 里直接调 embedding 接口，属于历史遗留，重构时下沉到 L2。

### L3 能力层

| 模块 | 路径 | 职责 | 状态 |
|---|---|---|---|
| 上下文管理 | `backend/context_manager.py` | 短期上下文 + 记忆注入 + 系统指令合并 | 已有 |
| 长期记忆 | `backend/memory_retriever.py` | JSONL 归档 + ChromaDB 向量检索 | 已有 |
| 人格引擎 | `backend/persona_engine.py`（待建） | 读取 persona yaml，生成人格相关上下文 | 规划 |
| 视觉管道 | `backend/vision_pipeline.py`（待建） | 图片→文字描述，喂给文本模型 | 规划 |
| 表情包管理 | `backend/sticker_manager.py`（待建） | 图库入库、打标、按情绪选图 | 规划 |
| 场景系统 | `backend/scenario_manager.py`（待建） | 场景配置注入，动态调整语气 | 规划 |

**规则**：L3 模块之间可横向调用（如 context_manager 调 memory_retriever），但不得依赖 L4/L5。

### L4 编排层

| 模块 | 路径 | 职责 |
|---|---|---|
| 对话编排 | `backend/orchestrator.py`（待建） | 一次完整对话：收消息→视觉预处理→建上下文→调模型→归档→返回 |

**规则**：L4 是唯一知道"一次对话从头到尾怎么走"的地方。L5 接入层只调 orchestrator，不自己拼流程。

### L5 接入层

| 模块 | 路径 | 职责 | 状态 |
|---|---|---|---|
| Web UI | `app/main.py` | Streamlit 界面 | 已有 |
| QQ 适配 | `adapters/qq_bot/`（待建） | NapCat OneBot 协议对接 | 规划 |
| CLI | `adapters/cli/`（待建） | 命令行测试入口 | 规划 |

**规则**：每个接入端独立目录，共享 L4 的 orchestrator。接入层只做协议转换，不写业务。

---

## 5. 核心数据流

### 一次对话（L4 视角）
用户消息↓[视觉预处理] 若含图片 → vision_pipeline 生成描述 → 拼入文本↓[上下文构建] context_manager.prepare_context()├─ 短期：最近 N 条├─ 长期：memory_retriever.search() 检索 Top-K└─ 人格：persona_engine 注入设定↓[模型推理] ollama_client.chat() → 模型输出↓[后处理] 表情标记替换、多气泡拆分↓[归档] memory_retriever.add_memory()↓返回给接入层发送text复制下载
### 多用户记忆隔离

- **session_id 规则**：Web UI 用固定 session；QQ 私聊用 `user_id`；QQ 群聊用 `group_id:user_id`
- `context_manager` 和 `memory_retriever` 的所有读写都必须带 `session_id`
- ChromaDB 的 metadata 存 `session_id`，检索时可按 session 过滤

---

## 6. 配置规范

`config/app_config.yaml` 现有字段：

| 路径 | 含义 |
|---|---|
| `model.base` | 基座模型名 |
| `model.persona` | 挂载 LoRA 后的人格模型名 |
| `model.embedding` | 嵌入模型名 |
| `ollama.url` / `chat_timeout` / `embedding_timeout` | Ollama 服务 |
| `generation.temperature` / `num_predict` / `num_ctx` / `think` | 推理参数 |
| `context.max_length` / `memory_enabled` | 上下文 |
| `memory.top_k` / `min_similarity` / `collection_name` / `bubble_separator` | 记忆检索 |
| `ui.*` | 界面 |

**新增配置规则**：
- 按功能分组，不散落
- 每个字段必须有注释说明单位/取值范围
- 本文档第 6 节同步更新

---

## 7. 编码与环境规范

### Python 环境（重要）

项目遵循“职责分离、环境内聚、可复现”原则。

| 环境 | 路径 | Python | 用途 |
|---|---|---|---|
| **运行环境** | `E:\Shelwell\.venv` | 3.12.7 | 跑 streamlit、chromadb、后端服务。**唯一运行环境** |
| **训练环境** | `C:\Users\<用户>\.unsloth\studio\unsloth_studio` | 3.12.7 | torch+ROCm+unsloth，由 Unsloth 官方安装器管理 |

**规则**：
- 运行一律用 `.venv`，通过 `start.bat` 激活，禁止用系统 Python 或 Anaconda base。
- 训练环境**不纳入项目 venv 管理**，因为 `amd-torch-device-gfx1031` 等设备包只存在于 AMD 私有索引，项目内从零复刻不可行。`requirements-train.txt` 仅作环境快照记录。
- 训练脚本调用时用训练环境的绝对路径 python。
- 新增运行依赖：`pip install` 后执行 `pip freeze > requirements.txt` 锁版本。
- 禁止把 venv 目录、模型、数据、日志纳入 git。

### 编码约定

- 文件编码 UTF-8，写文件时显式指定
- 模块头必须有注释：`# 路径` + `# 职责`
- 配置读取一律走 `config_loader.get()`，不直接读 yaml
- 异常必须捕获并打印带模块前缀的日志：`print(f"[Memory] xxx失败: {e}")`
- 新增依赖写入 `requirements.txt`

### 命名约定

- 模块：小写下划线 `memory_retriever.py`
- 类：大驼峰 `MemoryRetriever`
- 配置键：小写点分 `memory.top_k`
- session_id：`web` / `qq:{user_id}` / `qqg:{group_id}:{user_id}`

---

## 8. 扩展规则（新功能怎么加）

新增一个功能时，按顺序回答：

1. **属于哪一层？** 对照第 3 节分层表。
2. **是否需要新配置？** 是 → 加到 `app_config.yaml` 对应分组，更新第 6 节。
3. **是否需要新目录？** 只有 L5 接入端可以新增顶层目录 `adapters/<name>/`。L3/L4 新模块放 `backend/` 下。
4. **是否影响数据流？** 是 → 更新第 5 节数据流图。
5. **是否跨层调用？** 检查依赖方向是否自上而下。

### 未来功能归位示例

| 功能 | 层 | 位置 |
|---|---|---|
| QQ 接入 | L5 | `adapters/qq_bot/` |
| 图片理解 | L3 | `backend/vision_pipeline.py` |
| 表情包图库 | L3 | `backend/sticker_manager.py` |
| 场景切换 | L3 | `backend/scenario_manager.py` |
| LoRA 热切换 | L2 | `backend/llm/lora_loader.py` |
| 对话编排 | L4 | `backend/orchestrator.py` |
| 多模型路由 | L2 | `backend/llm/router.py` |
| 语音输入 | L5 | `adapters/voice/` |

---

## 9. 红线

- 禁止 L5 接入层直接调 L2/L1
- 禁止 L3 能力层 import L4/L5
- 禁止绕过 `config_loader` 直接读配置文件
- 禁止在业务代码里硬编码模型名、路径、URL
- 禁止把 `memory/`、`logs/`、`models/` 纳入版本控制（已在 `.gitignore`）
- 禁止同一功能出现两套实现（重构时删旧留新）
- 人格一致性以 `config/persona_prompts/shelwell_persona.yaml` 为唯一真相源，代码不得硬编码人格描述

---

## 10. 当前待办（按优先级）

已完成：
- [x] 环境重构：运行环境统一 `.venv`（3.12.7），依赖锁版本
- [x] L4 编排层：`backend/orchestrator.py`，接管上下文/推理/归档
- [x] L5 QQ 接入：`adapters/qq_bot/main.py`，NapCat 反向 WS，per-session 缓存

进行中 / 待办：
1. **训练集**：补到 300-500 条（现有 50 条 + 提示词文件已备）
2. **训练方案**：本地 12B QLoRA 受 12GB 显存限制，待定（降级 7B 或上云）
3. **L2 模型层封装**：抽出 `backend/llm/ollama_client.py`，消除 orchestrator 里的直接 requests
4. **人格 LoRA 挂载**：训练产出后接入 Ollama，切换 `model.persona`
5. **视觉/表情包**：方案待定，先留接口（`backend/vision_pipeline.py`、`sticker_manager.py`）
6. **QQ 多用户验证**：多人同时聊，验证 session 隔离和记忆正确性