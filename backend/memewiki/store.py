# backend/memewiki/store.py
# 职责：梗库存储与查询（SQLite 只读 + 五级匹配）
# 数据源：data/memes.db，6668 条，来自 cnmeme.wiki

import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

# 简单 LRU：key -> row，容量 512
_CACHE = {}
_CACHE_MAX = 512
_CACHE_LOCK = threading.Lock()


class MemeStore:
    """梗库只读查询"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base = Path(__file__).resolve().parent
            db_path = str(base / "data" / "memes.db")
        self.db_path = db_path
        self._conn = None
        self._lock = threading.Lock()

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        if not os.path.exists(self.db_path):
            print(f"[MemeWiki] 词库不存在: {self.db_path}")
            return None
        try:
            # check_same_thread=False 允许跨线程；只读打开
            self._conn = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=5)
            self._conn.row_factory = sqlite3.Row
            return self._conn
        except Exception as e:
            print(f"[MemeWiki] 打开词库失败: {e}")
            return None

    def available(self) -> bool:
        return self._connect() is not None

    # ---------- 查询（五级匹配）----------

    def lookup(self, query: str) -> Optional[dict]:
        """
        五级匹配，从严到宽：
        1. 完全一致
        2. 别名（逗号分隔，整项精确）
        3. 标题以查询开头（取最短）
        4. 查询里包含标题（取最长，最具体）
        5. 标题里包含查询（≥3字，或纯数字/字母≥2字）
        返回 dict 或 None。
        """
        q = (query or "").strip()
        if not q:
            return None

        # 缓存
        with _CACHE_LOCK:
            if q in _CACHE:
                return _CACHE[q]

        row = self._lookup_uncached(q)
        result = self._row_to_dict(row) if row else None

        with _CACHE_LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[q] = result
        return result

    def _lookup_uncached(self, q: str):
        conn = self._connect()
        if conn is None:
            return None
        with self._lock:
            try:
                # 1) 完全一致
                row = conn.execute(
                    "SELECT * FROM memes WHERE title = ? LIMIT 1", (q,)).fetchone()
                if row:
                    return row
                # 2) 别名
                row = conn.execute(
                    "SELECT * FROM memes WHERE aliases IS NOT NULL AND aliases != '' "
                    "AND instr(',' || lower(aliases) || ',', ',' || lower(?) || ',') > 0 LIMIT 1",
                    (q,)).fetchone()
                if row:
                    return row
                # 3) 标题前缀（取最短）
                row = conn.execute(
                    "SELECT * FROM memes WHERE lower(title) LIKE lower(?) || '%' "
                    "ORDER BY length(title) ASC LIMIT 1", (q,)).fetchone()
                if row:
                    return row
                # 4) 查询里包含标题（取最长）
                row = conn.execute(
                    "SELECT * FROM memes WHERE length(title) >= 2 AND instr(?, title) > 0 "
                    "ORDER BY length(title) DESC LIMIT 1", (q,)).fetchone()
                if row:
                    return row
                # 5) 标题里包含查询（≥3字；纯数字字母≥2字放宽）
                short_ok = len(q) >= 2 and re.fullmatch(r"[0-9a-zA-Z]+", q)
                if len(q) >= 3 or short_ok:
                    row = conn.execute(
                        "SELECT * FROM memes WHERE instr(title, ?) > 0 "
                        "ORDER BY length(title) ASC LIMIT 1", (q,)).fetchone()
                    if row:
                        return row
                return None
            except Exception as e:
                print(f"[MemeWiki] 查询失败: {e}")
                return None

    def batch_lookup_titles(self, titles: list) -> list:
        """批量查标题，返回命中的标题列表（精确匹配）"""
        if not titles:
            return []
        conn = self._connect()
        if conn is None:
            return []
        # SQLite IN 参数上限 999，分批
        hits = []
        batch_size = 500
        with self._lock:
            try:
                for i in range(0, len(titles), batch_size):
                    batch = titles[i:i + batch_size]
                    placeholders = ",".join("?" * len(batch))
                    rows = conn.execute(
                        f"SELECT title FROM memes WHERE title IN ({placeholders})",
                        batch).fetchall()
                    hits.extend(r["title"] for r in rows)
            except Exception as e:
                print(f"[MemeWiki] 批量查询失败: {e}")
        return hits

    # ---------- 格式化 ----------

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {k: (row[k] or "") for k in row.keys()}

    @staticmethod
    def _clip(s, n: int) -> str:
        t = re.sub(r"\s+", " ", str(s or "")).strip()
        return t[:n] + "…" if len(t) > n else t

    def format_entry(self, row: dict) -> str:
        """词条转给模型看的文本：怎么用放前面"""
        if not row:
            return ""
        title = str(row.get("title", ""))
        usage = self._clip(row.get("usage"), 700)
        summary = self._clip(row.get("summary") or row.get("origin"), 500)
        tags = self._clip(row.get("tags"), 120)
        lines = [f"[梗百科]「{title}」"]
        if tags:
            lines.append(f"标签：{tags}")
        if usage:
            lines.append(f"怎么用 / 怎么接：\n{usage}")
        if summary:
            lines.append(f"出处与含义：\n{summary}")
        return "\n".join(lines)

    def stats(self) -> dict:
        conn = self._connect()
        if conn is None:
            return {"available": False}
        try:
            with self._lock:
                n = conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0]
            return {"available": True, "total": n, "cached": len(_CACHE)}
        except Exception as e:
            return {"available": True, "error": str(e)}


# 单例
_STORE = None
_STORE_LOCK = threading.Lock()


def get_store() -> MemeStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = MemeStore()
    return _STORE