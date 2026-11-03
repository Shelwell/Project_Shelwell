# backend/memory/jobs.py
# 职责：L3 记忆系统的持久化后台任务队列
# 特点：SQLite 单文件、无第三方依赖、崩溃可恢复、支持指数退避重试
# 任务类型：index（索引构建）、consolidate（语义提炼）

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

# 任务状态
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"      # 超过最大重试次数，转人工

MAX_ATTEMPTS = 5
BASE_BACKOFF = 5       # 秒，第 n 次重试等待 BASE * 2^(n-1)


class JobQueue:
    """SQLite 任务队列"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base = Path(__file__).resolve().parent.parent.parent
            db_path = str(base / "memory" / "index" / "jobs.sqlite3")
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_db(self):
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    next_run_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # 同一 kind + dedup_key 只允许一个未完成的任务，避免重复入队
            con.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active
                ON jobs(kind, dedup_key)
                WHERE status IN ('pending', 'running')
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, next_run_at)")

    # ---------- 入队 ----------

    def enqueue(self, kind: str, payload: dict, dedup_key: str = None,
                delay: float = 0) -> Optional[int]:
        """
        入队一个任务。
        dedup_key 相同且已有未完成任务时跳过，返回 None。
        返回新任务 id，或 None（已存在）。
        """
        key = dedup_key or json.dumps(payload, sort_keys=True, ensure_ascii=False)
        now = time.time()
        try:
            with self._connect() as con:
                cur = con.execute(
                    """INSERT INTO jobs(kind, dedup_key, payload, status, next_run_at, created_at, updated_at)
                       VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                    (kind, key, json.dumps(payload, ensure_ascii=False), now + delay, now, now),
                )
                return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    # ---------- 认领 ----------

    def claim(self, kind: str = None) -> Optional[dict]:
        """取出一个到期可执行的任务并标记 running"""
        now = time.time()
        with self._connect() as con:
            if kind:
                row = con.execute(
                    """SELECT * FROM jobs
                       WHERE status='pending' AND next_run_at<=? AND kind=?
                       ORDER BY next_run_at LIMIT 1""", (now, kind)).fetchone()
            else:
                row = con.execute(
                    """SELECT * FROM jobs
                       WHERE status='pending' AND next_run_at<=?
                       ORDER BY next_run_at LIMIT 1""", (now,)).fetchone()
            if not row:
                return None
            con.execute(
                "UPDATE jobs SET status='running', updated_at=? WHERE id=?",
                (now, row["id"]))
            d = dict(row)
            d["status"] = RUNNING
            d["payload"] = json.loads(d["payload"])
            return d

    # ---------- 完成 / 失败 ----------

    def complete(self, job_id: int):
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status='done', updated_at=? WHERE id=?",
                (time.time(), job_id))

    def fail(self, job_id: int, error: str):
        """失败：未超上限则指数退避重试，否则转 failed 待人工"""
        now = time.time()
        with self._connect() as con:
            row = con.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            if attempts >= MAX_ATTEMPTS:
                con.execute(
                    """UPDATE jobs SET status='failed', attempts=?, last_error=?, updated_at=?
                       WHERE id=?""",
                    (attempts, error[:500], now, job_id))
            else:
                delay = BASE_BACKOFF * (2 ** (attempts - 1))
                con.execute(
                    """UPDATE jobs SET status='pending', attempts=?, last_error=?,
                       next_run_at=?, updated_at=? WHERE id=?""",
                    (attempts, error[:500], now + delay, now, job_id))

    # ---------- 恢复 ----------

    def recover_stale(self):
        """进程重启后，把卡在 running 的任务放回 pending"""
        now = time.time()
        with self._connect() as con:
            cur = con.execute(
                "UPDATE jobs SET status='pending', updated_at=? WHERE status='running'",
                (now,))
            return cur.rowcount

    # ---------- 统计 ----------

    def stats(self) -> dict:
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
            return {r["status"]: r["n"] for r in rows}