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
E:\Shelwell├── .venv/                    # 运行环境（Python 3.12.7）├── backend/                  # 后端核心│   ├── config_loader.py      # 部署期配置│   ├── runtime_config.py     # 运行期配置（runtime.yaml）│   ├── context_manager.py    # 上下文构建（三级优先级）│   ├── memory_retriever.py   # 长期记忆（ChromaDB + JSONL）│   ├── persona_engine.py     # 人格编译（persona + facts）│   ├── orchestrator.py       # L4 编排│   └── llm/│       ├── provider.py       # 统一推理接口（Ollama / OpenAI 兼容）│       └── usage.py          # 用量统计├── desktop/                  # 桌面管理控制台（PySide6）│   ├── main.py               # 主窗口 + 首页│   ├── worker.py             # 线程池后台任务│   ├── services.py           # 业务逻辑层│   ├── runtime_hook_ssl.py   # 打包 SSL 证书路径修正│   ├── ShelwellConsole.spec  # 打包配置│   └── pages/                # 功能页├── adapters/                 # 接入层│   └── qq_bot/               # QQ 接入（NapCat 反向 WS）├── config/                   # 配置│   ├── app_config.yaml       # 部署期静态配置│   ├── runtime.yaml          # 运行期可变配置（含密钥，gitignore）│   ├── runtime.example.yaml  # 脱敏模板│   └── persona_prompts/      # 人格与事实清单├── data/                     # 训练数据（gitignore）├── models/                   # 模型权重（gitignore）├── memory/                   # 运行时记忆（gitignore）├── scripts/                  # 工具脚本│   ├── generate_file_tree.py│   ├── manage_memory.py      # 记忆查看/搜索/删除│   ├── start_all.ps1         # 手动拉起全栈│   └── data_prep/├── logs/                     # 日志（gitignore）├── dist/                     # 打包产物（gitignore）├── ARCHITECTURE.md├── requirements.txt├── requirements-train.txt├── 文件树.md└── 大纲.txttext复制下载
外部资源：`E:\Tools\unsloth`（Unsloth 源码）、`C:\Users\<用户>\.unsloth\studio`（训练环境）、`E:\Shelwell\Ollama_models`（模型库）。

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
| 模型注册表 | `backend/llm/model_registry.py`（待建） | 可用模型清单、切换、加载确认 |
| 推理提供方 | `backend/llm/provider.py`（待建） | ollama / openai 兼容 API 统一接口 |
| LoRA 管理 | `backend/llm/lora_loader.py`（待建） | 挂载/卸载 LoRA，人格切换 |
| 嵌入器 | `backend/llm/embedder.py`（待建） | 文本向量化，供记忆模块调用 |

**规则**：L3 及以上**禁止直接 `import requests` 调 Ollama**，必须走 L2 封装。当前 `memory_retriever.py` 里直接调 embedding 接口，属于历史遗留，重构时下沉到 L2。

### L3 能力层

| 模块 | 路径 | 职责 | 状态 |
|---|---|---|---|
| 上下文管理 | `backend/context_manager.py` | 短期上下文 + 记忆注入 + 系统指令合并 | 已有 |
| 长期记忆 | `backend/memory_retriever.py` | JSONL 归档 + ChromaDB 向量检索 | 已有 |
| 知识库 | `backend/knowledge_base.py` | 外部知识 RAG（游戏/动漫/本地生活） | 规划 |
| 人格引擎 | `backend/persona_engine.py` | 读取 persona/facts yaml，生成人格上下文 | 已有 |
| 视觉管道 | `backend/vision_pipeline.py`（待建） | 图片→文字描述，喂给文本模型 | 规划 |
| 表情包管理 | `backend/sticker_manager.py`（待建） | 图库入库、打标、按情绪选图 | 规划 |
| 场景系统 | `backend/scenario_manager.py`（待建） | 场景配置注入，动态调整语气 | 规划 |
| 插件管理 | `backend/plugin_manager.py` | 插件发现/加载/钩子调度 | 规划 |

**规则**：L3 模块之间可横向调用（如 context_manager 调 memory_retriever），但不得依赖 L4/L5。

### L4 编排层

| 模块 | 路径 | 职责 | 状态 |
|---|---|---|---|
| 对话编排 | `backend/orchestrator.py` | 一次完整对话：建上下文→调模型→归档→返回 | 已有 |

**规则**：L4 是唯一知道"一次对话从头到尾怎么走"的地方。L5 接入层只调 orchestrator，不自己拼流程。

### L5 接入层

| 模块 | 路径 | 职责 | 状态 |
|---|---|---|---|
| Web UI | `app/main.py` | Streamlit 界面 | 已有 |
| QQ 适配 | `adapters/qq_bot/`（待建） | NapCat OneBot 协议对接 | 规划 |
| CLI | `adapters/cli/`（待建） | 命令行测试入口 | 规划 |
| 管理控制台 | `app/console.py` | 人格/模型/插件/账号/API 管理 | 规划 |

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

## 5.5 知识库设计

Shelwell 的“知识”分两层，与对话记忆完全隔离。

| 层 | 载体 | 内容 | 注入方式 |
|---|---|---|---|
| 短期：事实清单 | `config/persona_prompts/shelwell_facts.yaml` | 她熟悉的事物：游戏/动漫/成都生活细节 | 编译进系统提示词，常驻 |
| 长期：RAG 知识库 | ChromaDB `shelwell_knowledge` collection | 结构化知识条目（作品、地点、概念） | 按 query 检索 Top-K 注入 |

### 短期方案（先做）

在 persona yaml 旁边新增 `shelwell_facts.yaml`，列她“确实了解”的事物，例如游戏（明日方舟主练能天使）、动漫（心理测量者、攻壳机动队）、成都生活（南门鸡公煲、人民公园喝茶）。

`PersonaEngine` 读取后编译成【你熟悉的事】段落追加进提示词。目的不是让模型变博学，而是让它在自己的人设领域里有话可说，不瞎编。

### 长期方案（后做）

`backend/knowledge_base.py`（L3）：
- 独立 ChromaDB collection `shelwell_knowledge`，与对话记忆 `shelwell_memory` 隔离
- 数据源：手工整理的 Markdown/YAML 条目，`scripts/ingest_knowledge.py` 批量入库
- 检索接口 `search(query, top_k)`，`ContextManager` 在记忆检索之后追加一次知识检索
- 元数据含 `category`（game/anime/place/concept），可按场景过滤

**边界**：知识库只补事实，不改人格。人格永远来自 `shelwell_persona.yaml` + LoRA。

---
## 5.6 管理控制台（Shelwell Console，桌面版）

**定位**：`desktop/` 是**本地管理工具**，不是聊天界面。所有对话统一走 QQ（`adapters/qq_bot`），控制台只负责配置、切换、监控、内容管理。

技术栈：PySide6 6.8.x + PyInstaller（onedir）。零端口、无内嵌浏览器、可打包分发。

### 导航结构

| 页 | 职责 |
|---|---|
| 首页 | 一键启动全部服务、服务/容器/端口状态总览、日志查看、单项重启 |
| 人格管理 | persona 切换、facts 切换、LoRA 挂载、新建/删除人格、模板示例 |
| 模型切换 | Ollama 对话模型列表（过滤嵌入模型）、生成参数 |
| 插件管理 | 占位，待 `docs/plugin-spec.md` |
| QQ 账号 | NapCat WebUI 入口、Token 提取（持久化优先）、容器重启 |
| API 接入 | 服务商预设、模型下拉、推理强度、图片开关、上下文配置、连通测试、用量统计 |

### 分层
desktop/main.py        主窗口 + 首页（HomePage）desktop/worker.py      后台任务（线程池 + 信号），UI 不阻塞desktop/services.py    业务逻辑层，无 UI 依赖，复用 backenddesktop/pages/*.py     各功能页text复制下载
**约束**：
- UI 层不写业务逻辑，全部经 `services.py`
- 所有阻塞调用（PowerShell / HTTP）必须走 `run_async`
- 新增页面：`pages/` 下建模块 + `main.py` 的 `PAGES` 和堆栈同步

### 打包

- spec：`desktop/ShelwellConsole.spec`，onedir 模式
- 体积约 114MB
- **必须显式收集**：Anaconda 的 `libssl-3-x64.dll` / `libcrypto-3-x64.dll`（`_ssl.pyd` 依赖，不在默认搜索路径）、`certifi/cacert.pem`
- runtime hook `desktop/runtime_hook_ssl.py` 在冻结环境设置 `SSL_CERT_FILE`
- 项目根定位：`sys.frozen` 时从 exe 目录向上查找含 `config/app_config.yaml` 的目录

### 已移除

- Web 版控制台（`app/console.py`）
- 内嵌浏览器页（曾为 QtWebEngine，占 289MB，无实际用途）

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

`config/runtime.yaml`（运行期可变，控制台读写，与上面部署期配置分离）：

| 路径 | 含义 |
|---|---|
| `active.persona` | 当前人格（persona_prompts 下的文件名） |
| `active.lora` | 当前挂载的 LoRA 目录名，null 表示不挂载 |
| `active.provider` | `ollama` 或 `openai_compatible` |
| `active.model` | 当前模型名 |
| `active.plugins` | 启用的插件列表 |
| `providers.openai_compatible.*` | 远程 API 的 base_url / api_key / model |

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
| 多模型路由 | L2 | `backend/llm/router.py` |
| 语音输入 | L5 | `adapters/voice/` |
| 管理控制台 | L5 | `app/console.py` |
| 知识库 RAG | L3 | `backend/knowledge_base.py` |
| 插件系统 | L3 | `backend/plugin_manager.py` |
| 远程 API 接入 | L2 | `backend/llm/provider.py` |

---

## 8.5 部署与运维（Windows 服务化）

Shelwell 常驻运行由 Windows 服务托管，不依赖终端窗口。

| 组件 | 托管方式 | 自启 | 崩溃恢复 |
|---|---|---|---|
| Ollama | Windows 服务 `ShelwellOllama`（NSSM） | 自动 | NSSM 5 秒后重启 |
| QQ 适配层 | Windows 服务 `ShelwellQQBot`（NSSM） | 自动 | NSSM 5 秒后重启 |
| NapCat | Docker 容器 `napcat`，`--restart=always` | Docker Desktop 自启 | 进程崩溃自动重启 |
| Docker Desktop | 用户登录自启 | 是 | — |

**启动脚本**：`scripts/start_all.ps1` 只做“检查并拉起”，不常驻。首次部署、手动恢复时运行。

**日志位置**：
- `logs/launcher.log` — 启动脚本
- `logs/ollama.log` / `logs/ollama.err.log` — Ollama 服务
- `logs/qq_bot.log` / `logs/qq_bot.err.log` — QQ 适配层

**服务管理命令**：sc query ShelwellOllamasc query ShelwellQQBotnssm restart ShelwellQQBottext复制下载
**掉线处理（重要）**：
- 进程崩溃 → NSSM 或 Docker 自动恢复，无需干预。
- **QQ 被腾讯风控踢下线 → 无自动恢复手段，必须在 NapCat WebUI（`http://127.0.0.1:6099/webui`）重新扫码**。这是协议端方案的固有限制，不是配置问题。

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
- [x] 环境重构：`.venv`（3.12.7），依赖锁版本
- [x] L4 编排层 `orchestrator.py`
- [x] L5 QQ 接入 `adapters/qq_bot`，NapCat 反向 WS
- [x] L3 人格引擎 `persona_engine.py`，yaml + facts 驱动
- [x] L2 模型层 `provider.py`（Ollama / OpenAI 兼容）、`usage.py`
- [x] 运行期配置 `runtime_config.py` + `config/runtime.yaml`（含密钥，已 gitignore）
- [x] 桌面控制台 `desktop/`，PySide6 + PyInstaller，114MB
- [x] 服务化部署：NSSM 三服务 + Docker 自启
- [x] 短期知识：`shelwell_facts.yaml`

待办：
1. **API 预设模型名**：各家模型名已过时（DeepSeek `deepseek-chat` 已退役），需重新调研后更新 `API_PRESETS`
2. **聊天记录管理**：暂存 → 逐条审核 → 清除/归档/入记忆库
3. **API 页上下文占用布局**：进度条与标签排版仍异常，需重新设计
4. **插件规范**：研究 deepseek-harness，产出 `docs/plugin-spec.md`
5. **长期知识库**：`knowledge_base.py` + 入库脚本
6. **LoRA 挂载**：`lora_loader.py`，等训练产出
