# adapters 说明

> L5 接入层：把外部平台协议转换成 backend 的对话请求，再把回复发回去。
> 当前只有 QQ 接入；控制台在 `desktop/`，不在本层。

## 1. 边界

- `adapters/` 可以 import `backend/`，反向不允许
- 接入层只做：协议解析、会话 ID、发送、重连、平台事件
- 不拼人格提示词、不判定关系等级、不读写记忆库；这些走 `Orchestrator`
- 平台特有的字段（QQ 号、群号、消息 ID、引用）留在本层

## 2. 目录

| 目录/文件 | 职责 |
|---|---|
| `qq_bot/main.py` | OneBot 反向 WS、消息解析、图片、会话历史、发送 |
| `qq_bot/gate.py` | 群聊门禁执行器（active/shadow）与日志 |
| `qq_bot/engagement.py` | 群聊热度、接话时机 |
| `qq_bot/README.md` | QQ 接入详细说明 |

## 3. 部署形态

```text
NapCat（Docker） --反向 WS--> adapters/qq_bot/main.py --调用--> backend
                                      |
                                      └── 发送消息到 QQ
```

- QQ 接入通常作为 Windows 服务 `ShelwellQQBot` 运行
- NapCat 容器名 `napcat`，WebUI 端口由控制台 QQ 页管理
- 运行日志与群聊门禁/状态机日志在 `logs/`

详细端口、Token、服务配置见 [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md)。
