# backend/llm/usage.py
# 职责：记录推理调用的用量与状态，供控制台展示
# 进程内单例，重启清零（历史统计不持久化，避免额外依赖）

import threading
import time
from collections import deque

_LOCK = threading.Lock()
_MAX_RECORDS = 200


class UsageTracker:
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.records = deque(maxlen=_MAX_RECORDS)
                    cls._instance.last = None
        return cls._instance

    def record(self, provider: str, model: str, ok: bool,
               prompt_tokens: int = 0, completion_tokens: int = 0,
               elapsed: float = 0.0, error: str = ""):
        rec = {
            "ts": time.time(),
            "provider": provider,
            "model": model,
            "ok": ok,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "elapsed": round(elapsed, 2),
            "error": error,
        }
        with _LOCK:
            self.records.append(rec)
            self.last = rec

    def snapshot(self) -> dict:
        """汇总：最近一次、累计、成功率"""
        with _LOCK:
            recs = list(self.records)
        if not recs:
            return {"last": None, "count": 0, "total_tokens": 0,
                    "prompt_tokens": 0, "completion_tokens": 0,
                    "ok_rate": None, "avg_elapsed": None}
        ok_n = sum(1 for r in recs if r["ok"])
        return {
            "last": recs[-1],
            "count": len(recs),
            "total_tokens": sum(r["total_tokens"] for r in recs),
            "prompt_tokens": sum(r["prompt_tokens"] for r in recs),
            "completion_tokens": sum(r["completion_tokens"] for r in recs),
            "ok_rate": round(ok_n / len(recs) * 100, 1),
            "avg_elapsed": round(sum(r["elapsed"] for r in recs) / len(recs), 2),
        }

    def recent(self, n: int = 20) -> list:
        with _LOCK:
            return list(self.records)[-n:]


def get_tracker() -> UsageTracker:
    return UsageTracker()