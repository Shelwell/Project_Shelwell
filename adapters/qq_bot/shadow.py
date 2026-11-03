# adapters/qq_bot/shadow.py
# 职责：L5 适配层——群聊接话影子模式
# 说明：只记录"新策略会不会接话、为什么"，绝不发送、不改变现有行为
# 产物：logs/group_shadow.jsonl（每行一条决策，含特征，供事后校准阈值）

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from backend.group_policy import GroupGate, GroupMessage, load_interest_terms


class ShadowRecorder:
    """影子模式记录器：包一层 GroupGate，负责落盘与时间戳"""

    def __init__(self, cfg: dict, project_root: str = None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent.parent
        log_name = cfg.get("log_path") or "logs/group_shadow.jsonl"
        p = Path(log_name)
        self.log_path = p if p.is_absolute() else (self.root / p)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._lock = threading.Lock()
        self.gate = GroupGate(cfg, load_interest_terms())
        self.cfg = cfg

    # ---------- 观察 ----------

    def observe(self, *, group_id: str, sender_id: str, sender_name: str, text: str,
                ts: float = None, at_me: bool = False, at_other: bool = False,
                is_question: bool = False, reply_to_id: str = "",
                reply_to_sender: str = "", bot_name: str = "",
                legacy_engage: bool = False, msg_id: str = "") -> dict:
        """记录一条群消息的决策结果，返回决策字典（调用方只用于日志）"""
        if not self.enabled:
            return {}
        t0 = time.perf_counter()
        msg = GroupMessage(
            msg_id=msg_id, group_id=group_id, sender_id=sender_id,
            sender_name=sender_name, text=text, ts=ts or time.time(),
            at_me=at_me, at_other=at_other, is_question=is_question,
            reply_to_id=reply_to_id, reply_to_sender=reply_to_sender,
        )
        decision = self.gate.observe(msg, legacy_engage=legacy_engage,
                                     bot_name=bot_name)
        latency_ms = (time.perf_counter() - t0) * 1000

        rec = {
            "ts": datetime.fromtimestamp(msg.ts).isoformat(timespec="seconds"),
            "group": group_id,
            "msg_id": msg_id,
            "sender": sender_id,
            "sender_name": sender_name,
            "text": (text or "")[:300],
            "legacy_engage": legacy_engage,
            "decision": decision.to_dict(),
            "latency_ms": round(latency_ms, 2),
        }
        self._append(rec)
        return rec

    def on_bot_spoke(self, group_id: str, texts) -> None:
        """记录机器人自己说了什么，供后续判断"回复我"和话题连续性"""
        if not self.enabled:
            return
        text = "\n".join(texts) if isinstance(texts, (list, tuple)) else str(texts or "")
        self.gate.on_bot_spoke(group_id, text)

    # ---------- 落盘 ----------

    def _append(self, rec: dict):
        line = json.dumps(rec, ensure_ascii=False)
        try:
            with self._lock:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as e:
            print(f"[Shadow] 写入失败: {e}")

    def stats(self) -> dict:
        return {"enabled": self.enabled, "log": str(self.log_path)}
