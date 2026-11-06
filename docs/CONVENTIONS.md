# 开发规范

> 适用于 backend / adapters / desktop / scripts。目标是让“架构文档看全局、文件夹 README 看细节”能长期维持。

## 1. 环境

- Python **3.12.7**，虚拟环境 `.venv`
- 所有命令从项目根 `E:\Shelwell` 运行：

```powershell
.venv\Scripts\python.exe scripts\test_identity.py
```

- 文件统一 UTF-8；Windows 控制台输出用 `sys.stdout.reconfigure(encoding="utf-8")`
- 不在代码里写死 `E:\Shelwell`；用 `Path(__file__).resolve()` 推导项目根

## 2. 目录与命名

- 一层目录：`backend/`、`adapters/`、`desktop/`、`config/`、`scripts/`、`docs/`
- 二级目录：`backend/llm/`、`backend/memory/`、`backend/memewiki/`、`adapters/qq_bot/`、`desktop/pages/`、`config/persona_prompts/`
- 模块文件用 `snake_case.py`；类用 `PascalCase`；函数/变量 `snake_case`
- 测试命名 `scripts/test_<topic>.py`，必须能脱离 QQ/NapCat 运行

**新增文件时**：
1. 放到对应目录
2. 在根 [../文件树.md](../文件树.md) 加一行功能备注
3. 如果有独立子目录，建该目录 `README.md`
4. 只有跨模块/全局变化才改 [../ARCHITECTURE.md](../ARCHITECTURE.md)

## 3. 分层约束

```text
L5 adapters/desktop → L4 orchestrator → L3 backend → L2 llm → L1 config/fs
```

- L3 不 import L4/L5，不 import PySide6
- 对话回复只走 `Orchestrator`；语义提炼/视觉是独立模型调用，不走回复清洗与气泡
- 对话 prompt 只在 `ContextManager` / `Orchestrator` 拼；其他模块不要复制一套
- `adapters/` 可以 import `backend/`；`backend/` 不能 import `adapters/`
- `desktop/` 的业务逻辑放 `services.py`，页面只做展示

## 4. 配置

- 所有可调参数进 `config/app_config.yaml` 或 `config/runtime.yaml`
- 代码里用 `get("a.b.c", default)` / `get_runtime()`，不要硬编码阈值
- 新增配置项：
  1. 加默认值到 `app_config.yaml`
  2. 在 [../config/README.md](../config/README.md) 登记
  3. 代码读取
  4. 涉及控制台的，加输入项/保存逻辑
- 密钥只放 `runtime.yaml`，不写 README/日志/代码

## 5. 错误与日志

- 能力层失败尽量降级，不把整个对话打崩：例如记忆检索失败返回空、嵌入失败退回 FTS
- 对外返回结构化错误：`{"ok": False, "error": "..."}`；不要抛到 QQ 适配层
- 适配层捕获后写日志，给用户一句可理解的失败提示
- 调试信息用 `print` 时带模块前缀（`[Memory]`、`[Orchestrator]`）；长期运行的服务优先 `logging`
- **不要把完整 prompt/密钥写进普通日志**；需要看 prompt 用 `debug.trace_*`

## 6. 记忆/关系数据

- JSONL 是情景记忆真相；不要直接改 Chroma/FTS
- 关系计数由 `MemoryService.add_memory(direct=...)` 统一驱动；不要在别处重复计数
- 编辑已巩固情景会被拒绝；这是数据完整性约束，不是 bug
- 测试用临时目录，不写生产 `memory/`

## 7. 测试

- 每个能力至少一个 `scripts/test_*.py`
- 测试输出统一 `check(name, cond, detail)`，最后打印“全部通过”
- 涉及模型/网络的用例要 mock 或可跳过；CI/无 Ollama 环境不能挂
- 改以下模块必须跑对应测试：

| 改动 | 必跑 |
|---|---|
| 人格/触发词 | `test_identity` |
| 上下文拼接 | `test_context_freeze` |
| 记忆读写/编辑 | `test_memory_retrieval`、`test_memory_edit`、`test_memory_delete` |
| 关系 | `test_relationship` |
| 群聊门禁/状态机 | `test_gate_runner`、`test_group_shadow`、`test_group_state` |
| 回复格式/沉默 | `test_reply_short`、`test_silent` |
| 视觉 | `test_vision` |

## 8. Git 与生成物

- `runtime.yaml`、`memory/`、`logs/`、`dist/`、`data/`、`models/`、`.venv/` 不入库
- 不提交 API key、QQ Token、模型权重、用户聊天记录
- 大改前先确认工作区；提交前跑全量回归
- 文档与代码一起改：接口/参数变了，对应 README 同步

## 9. 文档

- **架构文档只写全局**：定位、分层、主链路、待办
- **文件树只做索引**：二级文件夹 + 文件功能备注
- **模块 README 写细节**：接口签名、参数、数据路径、错误、测试
- **docs/ 写跨模块**：设计、评审、规范、部署
- 新增/删除文件后更新 [../文件树.md](../文件树.md)
