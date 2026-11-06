# scripts/test_group_state.py
# 职责：最小版群聊状态机 shadow 的转移测试（不连接 QQ、不调模型）
# 用法：.venv\Scripts\python.exe scripts\test_group_state.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.group_state import (
    GroupStateMachine, STATE_IDLE, STATE_WATCH, STATE_QUEUED, STATE_COOLDOWN,
)

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


cfg = {
    "watch_seconds": 8.0,
    "strong_window_seconds": 1.2,
    "weak_window_seconds": 4.0,
    "max_wait_seconds": 10.0,
    "cooldown_seconds": 45.0,
    "min_interval_seconds": 20.0,
}
m = GroupStateMachine(cfg)

print("[1] 弱信号观察 → 超时")
r = m.observe("g1", ts=1000.0, weak=True, gate_respond=True,
              gate_reason="question", score=0.6)
check("弱信号进 WATCH", r["state_after"] == STATE_WATCH, r)
check("观察窗口 8s", abs(m.next_deadline("g1") - 1008.0) < 0.01,
      m.next_deadline("g1"))
r = m.finalize("g1", ts=1009.0)
check("观察超时且门禁会接 → 排队", r and r["action"] == "watch_timeout_queue"
      and r["state_after"] == STATE_QUEUED, r)
r = m.finalize("g1", ts=1013.0)
check("排队结束 → 生成/冷却", r and r["action"] == "generate"
      and r["state_after"] == STATE_COOLDOWN and r["would_respond"], r)
r = m.finalize("g1", ts=1059.0)
check("冷却结束 → 空闲", r and r["action"] == "cooldown_end"
      and r["state_after"] == STATE_IDLE, r)

print("\n[2] 弱信号超时但门禁不接 → 丢弃")
m2 = GroupStateMachine(cfg)
m2.observe("g", ts=2000.0, weak=True, gate_respond=False)
r = m2.finalize("g", ts=2009.0)
check("超时丢弃", r and r["action"] == "watch_timeout_drop"
      and r["state_after"] == STATE_IDLE, r)

print("\n[3] 强信号排队 + 合并")
m3 = GroupStateMachine(cfg)
r = m3.observe("g", ts=3000.0, strong=True, gate_respond=True,
               gate_reason="at_me", score=1.0)
check("强信号直接排队", r["state_after"] == STATE_QUEUED and r["merged"] == 1, r)
r = m3.observe("g", ts=3000.3, strong=False, weak=False, gate_respond=False)
check("排队中的无关消息不合并", r["action"] == "queue_ignore" and r["merged"] == 1, r)
r = m3.observe("g", ts=3000.5, strong=False, weak=True, gate_respond=True)
check("窗口内消息合并", r["action"] == "merge" and r["merged"] == 2, r)
check("排队窗口被延后", m3.next_deadline("g") > 3001.2, m3.next_deadline("g"))
r = m3.finalize("g", ts=3006.0)
check("合并后只生成一次", r and r["action"] == "generate" and r["merged"] == 2, r)

print("\n[4] 冷却与 @ 破冷却")
m4 = GroupStateMachine(cfg)
m4.observe("g", ts=4000.0, strong=True, gate_respond=True)
m4.finalize("g", ts=4002.0)   # generate -> cooldown until 4047
r = m4.observe("g", ts=4005.0, strong=True, gate_respond=True)
check("冷却内 @ 不够最小间隔 → 忽略", r["action"] == "cooldown_ignore", r)
r = m4.observe("g", ts=4025.0, strong=True, gate_respond=True)
check("冷却内 @ 超过最小间隔 → 破冷却排队",
      r["action"] == "break_cooldown_queue" and r["state_after"] == STATE_QUEUED, r)

print("\n[5] 关闭时无副作用")
off = GroupStateMachine(dict(cfg, enabled=False))
check("enabled=False 返回 None", off.observe("g", ts=5000.0, strong=True) is None)

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
