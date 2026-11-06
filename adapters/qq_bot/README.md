# adapters/qq_bot 说明

> QQ 接入层：NapCat / OneBot v11 反向 WebSocket 服务端。
> 只做协议转换和平台状态；对话、记忆、关系全部交给 `backend/`。

## 1. 文件

| 文件 | 职责 |
|---|---|
| `main.py` | 反向 WS、事件处理、私聊/群聊分流、图片、会话历史、发送、沉默旁白 |
| `gate.py` | `GroupGate` / `GateRunner`：门禁决策与 `logs/group_gate.jsonl` |
| `engagement.py` | 群聊热度、提问/话题/刷屏衰减，给旧策略和门禁提供信号 |

## 2. 启动

```powershell
.venv\Scripts\python.exe adapters\qq_bot\main.py
```

服务化部署时由 `ShelwellQQBot` 启动；NapCat 通过 OneBot v11 反向 WS 连到：

```text
ws://<本机>:8765/onebot
```

端口/路径在 `main.py` 顶部常量：`HOST=0.0.0.0`、`PORT=8765`、`PATH=/onebot`。

## 3. 消息流程

```text
OneBot event
  ├─ 私聊
  │    └─ Orchestrator.chat(direct=True)
  │
  └─ 群聊
       ├─ @/叫名字/回复她 → 强信号
       ├─ EngagementTracker 更新热度
       ├─ GroupGate 决策（active 时生效；shadow 只记录）
       ├─ GroupStateMachine 记录排队/合并/冷却（shadow，不改变发送）
       ├─ 接话：Orchestrator.chat(direct=True)
       └─ 不接：后台 archive_only（direct=@/回复)
```

### 会话 ID

| 场景 | session_id |
|---|---|
| 私聊 | `qq:<user_id>` |
| 群聊 | `qqg:<group_id>:<user_id>` |

同一用户在私聊/不同群的记忆按 session 隔离；关系按 `speaker_id` 跨 session 聚合。

- 私聊：session 历史用于 prompt
- 群聊：**所有发言人共享一份群短期历史**（用户消息带昵称，assistant 回复不带），让 prompt 前缀连续、提高缓存命中；记忆/关系归属仍按当前 `speaker_id`
- 群动态上下文只做补充，默认 20 条 / 3000 字
- 首次进群时从归档读取最近历史生成 C1 摘要；群历史估算 token 接近上限时自动总结，保留最近 30 条
- 没 @/点名的群消息也进入共享历史，并计入总结 token
- `primary_group_id` 指定的群档案/C1 会常驻；私聊在该前缀之后追加自己的对话

### 图片

- 图片在回复时随消息送模型；不接话的群消息也识别后归档
- `VisionPipeline` 负责指纹缓存、限频、小图护栏
- 图片识别失败只记录占位，不影响文本对话

### 沉默

- `Orchestrator` 返回 `silent=True` 时只发 `reply.silent_hint` 旁白
- 旁白不写进 AI 记忆；用户消息仍归档，关系计数照常
- 接入层用 `_last_silent` 避免把旁白当成她发言

## 4. 群聊门禁

`gate.py` 的 `GroupGate.decide(...)` 输入：

- 群、发送人、文本、时间
- `at_me` / `at_other` / `is_question`
- 引用的消息 ID 与被引用人
- 旧策略结果 `legacy_engage`
- 消息 ID（去重）

输出：

```python
{"respond": bool, "score": float, "reason": str, ...}
```

配置：

```yaml
qq_bot:
  group_gate:
    mode: active              # active=正式决策；shadow=只记录
    log_path: logs/group_gate.jsonl
    threshold: 0.55
    active_hours: ["19:00-24:00"]
    quiet_hours: ["00:30-09:00"]
    cooldown_seconds: 90
    rapid_window_seconds: 30
    rapid_min_msgs: 3
    min_len: 4
    name_aliases: ["西尔维娅", "西儿", "小西", "维娅"]
```

- `active`：`respond` 直接决定是否接话
- `shadow`：只写日志，实际仍用旧策略；用于对比
- 当前配置是 `active`；改前先看 `logs/group_gate.jsonl` 与 `scripts/report_shadow.py`

## 5. 群聊状态机（shadow）

`GroupStateMachine` 是**最小版**，只推演：

```text
IDLE → WATCH（弱信号观察）→ QUEUED（聚合/排队）→ COOLDOWN
```

- 强信号：@她 / 回复她 / 叫名字 → 直接 QUEUED
- 弱信号：提问 / 门禁评分达到候选线 → WATCH
- QUEUED 内继续到达的信号合并，`max_wait_seconds` 封顶
- COOLDOWN 内弱信号忽略；强信号超过最小间隔可破冷却
- 当前 `mode=shadow`：只写 `logs/group_state.jsonl`，不改变实际发送

配置：

```yaml
qq_bot:
  group_state:
    enabled: true
    mode: shadow
    log_path: logs/group_state.jsonl
    watch_seconds: 8
    strong_window_seconds: 1.2
    weak_window_seconds: 4
    max_wait_seconds: 10
    cooldown_seconds: 45
    min_interval_seconds: 20
```

**已知限制**：当前 adapter 是“收一条 → 等模型 → 发一条”，模型生成期间新消息不会进入状态机。shadow 数据能反映转移，但热聊合并样本偏少；切 active 前需要把收消息和生成拆成并发任务。

## 6. 自动接话热度

`engagement.py` 提供旧策略信号：

- @ 她：最高热度
- 提问：加分
- 命中话题：加分
- 普通消息：少量加分
- 短时间刷屏：衰减
- 活跃/静默时段：门禁和旧策略共用

参数在 `qq_bot.auto_engage.*`，包括 `heat_threshold`、`max_heat`、`heat_*`、`skip_short_len`、`conversation_threshold`、`conversation_window_minutes`、`half_life_minutes`。

## 7. 关系计数

- 私聊、@她、回复她、叫名字、门禁决定接话 → `direct=True`
- 群聊旁观 → `direct=False`，只增加 `msg_count`
- 群里 @她但门禁没接，也会在 `archive_only` 里按 `direct=True` 归档
- 关系等级由 `backend/relationship.py` 计算，本层只传标记

## 8. 部署

- Windows 服务：`ShelwellQQBot`（NSSM）
- NapCat Docker 容器：`napcat`
- 反向 WS 地址在 NapCat WebUI 配置为 `ws://host.docker.internal:8765/onebot`（Windows Docker）
- 详细端口、Token、重启命令见 [../../docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md)

## 9. 常见问题

| 现象 | 排查 |
|---|---|
| 连不上 NapCat | 检查 8765 端口、反向 WS 地址、Windows 防火墙 |
| 收到消息不回 | 看 `logs/group_gate.jsonl` 的 `respond/reason`；看是否静默时段 |
| 回复重复 | 检查 `message_cache`/`msg_id` 去重；看门禁日志是否重复触发 |
| 状态机无日志 | 确认 `qq_bot.group_state.enabled=true`，且有群消息进入 |
| 图片不识别 | 检查视觉模型、图片大小/频率限制、`memory/index/vision_cache.sqlite3` |

## 10. 测试

```powershell
.venv\Scripts\python.exe scripts\test_gate_runner.py
.venv\Scripts\python.exe scripts\test_group_shadow.py
.venv\Scripts\python.exe scripts\test_group_state.py
```

完整清单见 [../../scripts/README.md](../../scripts/README.md)。
