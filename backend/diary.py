# backend/diary.py
# 职责：按 scope 追加第一人称日记；C1 从日记生成，不再反复改写总摘要。

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from backend.config_loader import get


class DiaryStore:
    def __init__(self, path: str = None):
        root = Path(__file__).resolve().parent.parent
        p = Path(path or get("diary.path", "memory/diaries.jsonl")
                 or "memory/diaries.jsonl")
        self.path = p if p.is_absolute() else root / p
        self._lock = threading.Lock()

    def append(self, scope: str, content: str, participants: list = None,
               date: str = "") -> dict:
        content = str(content or "").strip()
        if not content:
            return {}
        rec = {
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "scope": str(scope or "default"),
            "participants": [str(x) for x in (participants or [])],
            "content": content,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def recent(self, scope: str, limit: int = 3) -> list:
        if not self.path.exists():
            return []
        out = []
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if str(rec.get("scope", "")) == str(scope):
                        out.append(rec)
        return out[-max(1, int(limit or 3)):]

    def format_recent(self, scope: str, limit: int = 3, max_chars: int = 1200) -> str:
        rows = self.recent(scope, limit)
        if not rows:
            return ""
        lines = []
        for r in rows:
            lines.append("【日记 %s】\n%s" % (r.get("date", ""), r.get("content", "")))
        text = "\n\n".join(lines)
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text
