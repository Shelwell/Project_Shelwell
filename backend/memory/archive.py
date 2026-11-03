# backend/memory/archive.py
# 职责：L3 情景记忆的权威存储层（JSONL 真相源）
# 原则：ChromaDB 只是索引，可重建；JSONL 永不丢失，删除需显式调用

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.memory.models import MemoryEvent


class Archive:
    """情景记忆 JSONL 归档"""

    def __init__(self, history_dir: Optional[str] = None):
        if history_dir is None:
            base = Path(__file__).resolve().parent.parent.parent
            history_dir = str(base / "memory" / "history")
        self.history_dir = history_dir
        os.makedirs(self.history_dir, exist_ok=True)

    # ---------- 写入 ----------

    def append(self, event: MemoryEvent) -> bool:
        """追加一条情景记忆到当天文件"""
        try:
            day = event.timestamp[:10]  # YYYY-MM-DD
            path = os.path.join(self.history_dir, f"{day}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            print(f"[Archive] 写入失败: {e}")
            return False

    # ---------- 查询 ----------

    def _iter_all(self):
        """遍历所有 JSONL 记录"""
        if not os.path.isdir(self.history_dir):
            return
        for name in sorted(os.listdir(self.history_dir)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(self.history_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield MemoryEvent.from_dict(json.loads(line))
                        except Exception:
                            continue
            except Exception as e:
                print(f"[Archive] 读取 {name} 失败: {e}")

    def get(self, event_id: str) -> Optional[MemoryEvent]:
        """按 ID 查询单条"""
        for ev in self._iter_all():
            if ev.id == event_id:
                return ev
        return None

    def list_by_session(self, session_id: str, limit: int = 100) -> list:
        """按会话查询，返回最近的 limit 条"""
        hits = [ev for ev in self._iter_all() if ev.session_id == session_id]
        return hits[-limit:]

    def list_recent_by_channel(self, channel_id: str, limit: int = 15) -> list:
        """按 channel_id 拉取最近的 N 条记录（用于群聊上下文），返回正序（旧→新）"""
        hits = [ev for ev in self._iter_all() if ev.channel_id == channel_id]
        # 按时间排序后取尾部 N 条，再转回正序
        hits.sort(key=lambda e: e.timestamp)
        return hits[-limit:] if len(hits) > limit else hits

    def list_unconsolidated(self, min_score: float = 0.0, limit: int = 200) -> list:
        """列出尚未被语义提炼消费的情景记忆"""
        out = []
        for ev in self._iter_all():
            if ev.consolidated or ev.score < min_score:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
        return out

    # ---------- 删除 ----------

    def delete(self, event_id: str) -> bool:
        """删除单条（重写所在天的文件）"""
        target = None
        for ev in self._iter_all():
            if ev.id == event_id:
                target = ev
                break
        if not target:
            return False
        day = target.timestamp[:10]
        path = os.path.join(self.history_dir, f"{day}.jsonl")
        return self._rewrite_without(path, {event_id})

    def _rewrite_without(self, path: str, drop_ids: set) -> bool:
        """重写文件，剔除指定 ID"""
        if not os.path.exists(path):
            return False
        tmp = path + ".tmp"
        try:
            kept = 0
            with open(path, "r", encoding="utf-8") as src, \
                 open(tmp, "w", encoding="utf-8") as dst:
                for line in src:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        dst.write(line + "\n")
                        continue
                    if d.get("id") in drop_ids:
                        continue
                    dst.write(json.dumps(d, ensure_ascii=False) + "\n")
                    kept += 1
            os.replace(tmp, path)
            return True
        except Exception as e:
            print(f"[Archive] 重写失败: {e}")
            if os.path.exists(tmp):
                os.remove(tmp)
            return False

    # ---------- 统计 ----------

    def stats(self) -> dict:
        total = 0
        sessions = set()
        earliest = None
        latest = None
        for ev in self._iter_all():
            total += 1
            sessions.add(ev.session_id)
            if earliest is None or ev.timestamp < earliest:
                earliest = ev.timestamp
            if latest is None or ev.timestamp > latest:
                latest = ev.timestamp
        return {
            "total": total,
            "sessions": len(sessions),
            "earliest": earliest,
            "latest": latest,
        }