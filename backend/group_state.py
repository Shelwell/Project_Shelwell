# backend/group_state.py
# 职责：L3 群聊接话状态机（最小版 · shadow）
#
# 只做“什么时候该接、连续消息合并多久、冷却多久”的状态推演，
# 不在 shadow 阶段改变实际发送；adapter 把每次状态转移写入 logs/group_state.jsonl。
#
# 状态：IDLE → WATCH（弱信号观察）→ QUEUED（已排队/聚合）→ COOLDOWN
# 强信号：@她 / 回复她 / 叫名字；弱信号：提问 / 门禁评分达到候选线
# 具体文本仍由 Orchestrator / 人设决定，状态机不写死回复。

from __future__ import annotations

import time
from typing import Optional

STATE_IDLE = "idle"
STATE_WATCH = "watch"
STATE_QUEUED = "queued"
STATE_COOLDOWN = "cooldown"

DEFAULTS = {
    "enabled": True,
    "mode": "shadow",
    "log_path": "logs/group_state.jsonl",
    "watch_seconds": 8.0,          # 弱信号观察窗口
    "strong_window_seconds": 1.2,  # @/回复她的聚合窗口
    "weak_window_seconds": 4.0,    # 弱信号的聚合窗口
    "max_wait_seconds": 10.0,      # 排队最长等待，防止无限合并
    "cooldown_seconds": 45.0,      # 一次发言后的全局冷却
    "min_interval_seconds": 20.0,  # @ 破冷却的最小间隔
}


class GroupStateMachine:
    """每个群一份状态的轻量状态机（shadow 只记录，不发送）"""

    def __init__(self, cfg: dict = None):
        c = dict(DEFAULTS)
        c.update(cfg or {})
        self.enabled = bool(c.get("enabled", True))
        self.mode = str(c.get("mode") or "shadow").lower()
        self.watch_seconds = float(c.get("watch_seconds", 8.0))
        self.strong_window = float(c.get("strong_window_seconds", 1.2))
        self.weak_window = float(c.get("weak_window_seconds", 4.0))
        self.max_wait = float(c.get("max_wait_seconds", 10.0))
        self.cooldown_seconds = float(c.get("cooldown_seconds", 45.0))
        self.min_interval = float(c.get("min_interval_seconds", 20.0))
        self._groups = {}

    # ---------- 状态 ----------

    def _g(self, group_id: str) -> dict:
        g = self._groups.get(group_id)
        if g is None:
            g = {
                "state": STATE_IDLE,
                "watch_until": 0.0,
                "queue_until": 0.0,
                "queue_start": 0.0,
                "cooldown_until": 0.0,
                "last_reply": 0.0,
                "merged": 0,
                "watch_hits": 0,
                "last_gate_respond": False,
                "last_gate_reason": "",
                "last_score": 0.0,
            }
            self._groups[group_id] = g
        return g

    def _queue(self, g: dict, now: float, strong: bool) -> None:
        if g["state"] != STATE_QUEUED:
            g["merged"] = 1
            g["queue_start"] = now
        g["state"] = STATE_QUEUED
        window = self.strong_window if strong else self.weak_window
        until = now + window
        cap = g["queue_start"] + self.max_wait
        g["queue_until"] = min(until, cap)

    def _record(self, group_id: str, now: float, before: str, event: str,
                action: str, g: dict, would_respond: bool,
                reason: str = "") -> dict:
        return {
            "ts": round(now, 3),
            "group_id": group_id,
            "state_before": before,
            "event": event,
            "action": action,
            "state_after": g["state"],
            "would_respond": bool(would_respond),
            "merged": int(g["merged"]),
            "gate_respond": bool(g["last_gate_respond"]),
            "gate_reason": g["last_gate_reason"],
            "score": round(float(g["last_score"] or 0.0), 3),
            "reason": reason,
            "pending_until": self.next_deadline(group_id),
        }

    # ---------- 消息入口 ----------

    def observe(self, group_id: str, ts: float = None, strong: bool = False,
                weak: bool = False, gate_respond: bool = False,
                gate_reason: str = "", score: float = 0.0,
                sender: str = "", text: str = "") -> Optional[dict]:
        """
        每条群消息调一次；更新状态并返回一条 shadow 记录。
        strong/weak 只影响状态推演，不改变 adapter 的实际回复决策。
        """
        if not self.enabled:
            return None
        now = float(ts if ts is not None else time.time())
        g = self._g(group_id)
        before = g["state"]
        if strong or weak:
            g["last_gate_respond"] = bool(gate_respond)
            g["last_gate_reason"] = str(gate_reason or "")
            g["last_score"] = float(score or 0.0)
        action = "ignore"
        would = False

        if g["state"] == STATE_IDLE:
            if strong:
                self._queue(g, now, strong=True)
                action = "queue_strong"
                would = True
            elif weak:
                g["state"] = STATE_WATCH
                g["watch_until"] = now + self.watch_seconds
                g["watch_hits"] = 1
                action = "watch_start"
            else:
                action = "ignore"

        elif g["state"] == STATE_WATCH:
            if strong:
                self._queue(g, now, strong=True)
                action = "queue_from_watch"
                would = True
            elif weak:
                g["watch_hits"] += 1
                g["watch_until"] = max(g["watch_until"], now + self.watch_seconds)
                action = "watch_update"
            else:
                action = "watch_ignore"

        elif g["state"] == STATE_QUEUED:
            if not (strong or weak):
                action = "queue_ignore"
            else:
                # 聚合窗口内继续合并；不重复生成
                g["merged"] += 1
                window = self.strong_window if strong else self.weak_window
                cap = g["queue_start"] + self.max_wait
                g["queue_until"] = max(g["queue_until"],
                                       min(now + window, cap))
                action = "merge"
                would = True

        elif g["state"] == STATE_COOLDOWN:
            if strong and now - g["last_reply"] >= self.min_interval:
                self._queue(g, now, strong=True)
                action = "break_cooldown_queue"
                would = True
            else:
                action = "cooldown_ignore"

        return self._record_with_text(
            group_id, now, before,
            "strong" if strong else ("weak" if weak else "none"),
            action, g, would, sender, text,
            reason=str(gate_reason or ""))

    def _record_with_text(self, group_id, now, before, event, action, g,
                          would, sender, text, reason="") -> dict:
        rec = self._record(group_id, now, before, event, action, g, would,
                           reason=reason)
        rec["sender"] = str(sender or "")
        rec["text"] = str(text or "")[:80]
        return rec

    # ---------- 定时结束 ----------

    def next_deadline(self, group_id: str) -> Optional[float]:
        g = self._groups.get(group_id)
        if not g:
            return None
        if g["state"] == STATE_WATCH:
            return g["watch_until"]
        if g["state"] == STATE_QUEUED:
            return g["queue_until"]
        if g["state"] == STATE_COOLDOWN:
            return g["cooldown_until"]
        return None

    def finalize(self, group_id: str, ts: float = None) -> Optional[dict]:
        """
        到点推进一步：观察超时 → 排队/丢弃；排队结束 → 冷却；冷却结束 → 空闲。
        由 adapter 的 asyncio timer 调用；测试可直接传 ts。
        """
        if not self.enabled:
            return None
        g = self._groups.get(group_id)
        if not g:
            return None
        now = float(ts if ts is not None else time.time())
        before = g["state"]
        action = ""
        would = False

        if g["state"] == STATE_WATCH and now >= g["watch_until"]:
            if g["last_gate_respond"]:
                self._queue(g, now, strong=False)
                action = "watch_timeout_queue"
                would = True
            else:
                g["state"] = STATE_IDLE
                action = "watch_timeout_drop"

        elif g["state"] == STATE_QUEUED and now >= g["queue_until"]:
            g["state"] = STATE_COOLDOWN
            g["cooldown_until"] = now + self.cooldown_seconds
            g["last_reply"] = now
            action = "generate"
            would = True
            result = self._record(group_id, now, before, "timer", action, g,
                                  would, reason="queue_window_end")
            g["merged"] = 0
            return result

        elif g["state"] == STATE_COOLDOWN and now >= g["cooldown_until"]:
            g["state"] = STATE_IDLE
            action = "cooldown_end"

        else:
            return None

        return self._record(group_id, now, before, "timer", action, g, would)

    def state_of(self, group_id: str) -> str:
        g = self._groups.get(group_id)
        return g["state"] if g else STATE_IDLE
