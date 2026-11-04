# adapters/qq_bot/gate.py
# 职责：L5 适配层——群聊接话门禁的执行器
# 两种模式：
#   active ：用 GroupGate 的决策直接决定"接不接"（旧热度只用于模型驻留建议）
#   shadow ：只记录决策、不改变行为（用于校准阈值）
# 每一条群消息都会写日志，便于事后审计与对比

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from backend.group_policy import GroupGate, GroupMessage, load_interest_terms


class GateRunner:
    """群聊接话门禁：决策 + 落盘"""

    def __init__(self, cfg: dict, project_root: str = None):
        cfg = cfg or {}
        self.cfg = cfg
        self.mode = str(cfg.get("mode") or "shadow").strip().lower()
        self.enforce = (self.mode == "active")

        self.root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent.parent
        p = Path(cfg.get("log_path") or "logs/group_gate.jsonl")
        self.log_path = p if p.is_absolute() else (self.root / p)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        self._lock = threading.Lock()
        self.gate = GroupGate(cfg, load_interest_terms())

    # ---------- 决策 ----------

    def decide(self, *, group_id: str, sender_id: str, sender_name: str, text: str,
               ts: float = None, at_me: bool = False, at_other: bool = False,
               is_question: bool = False, reply_to_id: str = "",
               reply_to_sender: str = "", bot_name: str = "",
               legacy_engage: bool = False, msg_id: str = "") -> dict:
        """给出决策并写日志，返回 {"respond", "reason", "score", ...}"""
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
            "mode": self.mode,
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
        """记录机器人自己说了什么，供判断"回复我"和话题连续性"""
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
            print(f"[Gate] 写入失败: {e}")

    def stats(self) -> dict:
        return {"mode": self.mode, "log": str(self.log_path)}
