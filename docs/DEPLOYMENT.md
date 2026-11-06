# 部署说明

> Shelwell 是单机 Windows 部署：Ollama、QQ 接入、NapCat 容器、桌面控制台。
> 具体配置字段见 [../config/README.md](../config/README.md)；QQ 行为见 [../adapters/qq_bot/README.md](../adapters/qq_bot/README.md)。

## 1. 组件与端口

| 组件 | 形态 | 端口/入口 | 说明 |
|---|---|---|---|
| Ollama | Windows 服务 `ShelwellOllama` | `11434` | 本地 27B / 嵌入模型 |
| QQ 接入 | Windows 服务 `ShelwellQQBot` | `8765` | OneBot v11 反向 WS |
| NapCat | Docker 容器 `napcat` | WebUI `6099` | 登录 QQ，反连 QQ 接入 |
| 控制台 | `ShelwellConsole.exe` | 无端口 | 管理服务/模型/记忆 |
| 群聊 trace | 文件 | `logs/group_gate.jsonl` / `logs/group_state.jsonl` | 门禁/状态机实验数据 |
| 模型 trace | 文件 | `logs/llm_trace/` | 输入/输出/错误 |

## 2. 启动顺序

推荐用控制台“一键启动”，它按以下顺序：

1. 启动 Docker Desktop（如未运行，最多等 120s）
2. 清理抢占 11434 的游离 `ollama` 进程
3. 启动 `ShelwellOllama`
4. 启动 `ShelwellQQBot`
5. 启动 `napcat` 容器

手动：

```powershell
Start-Service ShelwellOllama
Start-Service ShelwellQQBot
docker start napcat
```

停止顺序相反：先停 QQBot/NapCat，最后停 Ollama（避免请求打到已停模型）。

## 3. Ollama

- 模型目录：`Ollama_models/`（或由 `OLLAMA_MODELS` 指定）
- 天气状态：`memory/weather_state.json`（运行时自动生成；删除会重新生成当天天气）
- 环境变量（NSSM 服务）：
  - `OLLAMA_HOST=0.0.0.0:11434`（如需要局域网访问）
  - `OLLAMA_MODELS=E:\Shelwell\Ollama_models`
  - `OLLAMA_KEEP_ALIVE=0.5h`（与 `generation.keep_alive` 配合）
  - `OLLAMA_NUM_THREADS=12`（16 核 CPU，留 4 核给系统/控制台）
- 修改服务环境变量：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\set_ollama_env.ps1
Restart-Service ShelwellOllama
```

- 验证：

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
ollama ps
```

- 冷加载 27B 约 50 秒，`keep_alive` 建议保持 0.5h，不要频繁卸载

## 4. NapCat / OneBot

- 容器名：`napcat`
- 数据目录：`adapters/qq_bot/napcat_data/`
- WebUI：`http://127.0.0.1:6099/webui`
- Token：`adapters/qq_bot/napcat_data/webui.json`；控制台 QQ 页可读取并打开带 token 的 WebUI
- 反向 WS 地址（NapCat 内配置）：

```text
ws://host.docker.internal:8765/onebot
```

- 如果 NapCat 跑在宿主机而不是 Docker，用 `ws://127.0.0.1:8765/onebot`
- Windows 防火墙需放行 `8765`（入站）
- 验证：

```powershell
Test-NetConnection 127.0.0.1 -Port 8765
docker logs --tail 200 napcat
```

## 5. Windows 服务（NSSM）

现有服务：

```powershell
Get-Service ShelwellOllama, ShelwellQQBot
```

常见操作：

```powershell
Restart-Service ShelwellOllama
Restart-Service ShelwellQQBot
```

- 服务由 NSSM 托管，`nssm.exe` 路径以本机安装为准
- QQBot 服务的 `AppDirectory` 必须是项目根 `E:\Shelwell`，否则找不到 `config/` 和 `adapters/`
- 服务账户需要有项目目录读写权限（`memory/`、`logs/`）
- 不要用 `python desktop/main.py` 当服务；控制台是交互程序

## 6. 控制台

- 源码：`.venv\Scripts\python.exe desktop\main.py`
- 打包：`dist\ShelwellConsole\ShelwellConsole.exe`
- 启停服务需要管理员权限；打包版启动时请求 UAC
- 分发时带整个项目目录（或至少保证 exe 位于项目根的 `dist/` 下）
- 构建：

```powershell
.venv\Scripts\python.exe -m PyInstaller desktop\ShelwellConsole.spec --noconfirm
```

## 7. 目录与持久化

| 目录 | 是否必须保留 | 说明 |
|---|---|---|
| `config/runtime.yaml` | 是 | 模型/API/依赖对象，含密钥 |
| `memory/history/` | 是 | 情景记忆真相 |
| `memory/index/` | 可重建 | 向量/FTS/语义/关系；关系库也可从归档回填 |
| `adapters/qq_bot/napcat_data/` | 是 | NapCat 登录态/配置 |
| `logs/` | 可清 | 运行日志、门禁/状态机/trace |
| `models/` | 按需 | LoRA/权重 |
| `Ollama_models/` | 按需 | Ollama 模型 |

备份最小集：`config/runtime.yaml` + `memory/history/` + `adapters/qq_bot/napcat_data/`。

## 8. 日志与排查

| 文件 | 看什么 |
|---|---|
| 控制台首页日志 | 服务启动、端口、Ollama 拉取 |
| `logs/group_gate.jsonl` | 门禁 `respond/reason/score` |
| `logs/group_state.jsonl` | 状态机 `action/state_after/merged` |
| `logs/llm_trace/index.jsonl` | 模型调用是否成功、耗时、错误 |
| `docker logs napcat` | QQ 登录/断线/反向 WS |

常见问题：

- **11434 被占用**：控制台首页“清理游离 ollama 进程”，或手动结束非服务进程
- **QQBot 启动即退出**：看服务日志/项目根是否配对、`config` 是否可读、8765 是否被占
- **NapCat 连不上**：检查 `host.docker.internal`、防火墙、QQBot 是否监听 `0.0.0.0:8765`
- **控制台打不开**：确认 exe 在项目 `dist/` 下，能向上找到 `config/app_config.yaml`

## 9. 升级/回滚

1. 停 `ShelwellQQBot`、`napcat`
2. 备份 `config/runtime.yaml`、`memory/history/`、`napcat_data/`
3. 替换代码/控制台
4. 如索引结构变了，跑 `scripts/rebuild_index.py`
5. 启动 Ollama → QQBot → NapCat
6. 用控制台“调试”页看一轮真实对话的输入/输出/错误

## 10. 安全

- `runtime.yaml` 含 API key，不入库、不贴日志
- NapCat Token 不入库
- 控制台/服务不要暴露到公网；8765 只对本机/局域网开放
- QQ 聊天记录属于隐私；`logs/llm_trace/` 默认保留 7 天，可关闭或手动清空
