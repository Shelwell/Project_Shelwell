# backend/memory/indexer.py
# 职责：L3 情景记忆的双索引——向量索引(ChromaDB) + 关键词索引(SQLite FTS5)
# 原则：索引可重建，真相在 archive.py 的 JSONL

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

import chromadb
import jieba

from backend.memory.embedder import Embedder
from backend.memory.models import MemoryEvent


def seg(text: str) -> str:
    """中文分词：用 cut_for_search 兼顾细粒度与长词，结果空格连接供 FTS5 索引"""
    return " ".join(jieba.cut_for_search(text or ""))


def seg_query(text: str) -> str:
    """查询用分词：每个词加双引号，防止 [] () " * 等 FTS5 特殊字符导致语法错误"""
    words = [w for w in jieba.cut_for_search(text or "") if w.strip()]
    if not words:
        return ""
    return " ".join('"' + w.replace('"', '""') + '"' for w in words)


class Indexer:
    """情景记忆双索引"""

    def __init__(self, chroma_dir: Optional[str] = None,
                 fts_db: Optional[str] = None,
                 collection_name: str = "shelwell_episodic"):
        base = Path(__file__).resolve().parent.parent.parent
        chroma_dir = chroma_dir or str(base / "memory" / "vector_store" / "chroma_db")
        fts_db = fts_db or str(base / "memory" / "index" / "memory_fts.sqlite3")

        os.makedirs(os.path.dirname(fts_db), exist_ok=True)

        self.client = chromadb.PersistentClient(path=chroma_dir)
        self.collection = self.client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"})
        self.embedder = Embedder()

        self.fts_path = fts_db
        self._init_fts()

    # ---------- FTS5 初始化 ----------

    def _connect_fts(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.fts_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_fts(self):
        with self._connect_fts() as con:
            con.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_episodic
                USING fts5(id UNINDEXED, seg, tokenize='unicode61')
            """)

    # ---------- 写入 ----------

    def index(self, event: MemoryEvent) -> dict:
        """
        为一条情景记忆建立双索引。返回 {"vector": bool, "lexical": bool}
        """
        result = {"vector": False, "lexical": False}

        # 1. 向量索引
        vec = self.embedder.embed_safe(event.combined_text)
        if vec:
            try:
                self.collection.upsert(
                    ids=[event.id],
                    embeddings=[vec],
                    documents=[event.combined_text],
                    metadatas=[{
                        "timestamp": event.timestamp,
                        "session_id": event.session_id,
                        "speaker_id": event.speaker_id,
                        "channel_type": event.channel_type,
                        "channel_id": event.channel_id,
                        "score": event.score,
                        "user_msg": event.user_msg[:200],
                        "ai_msg": (event.ai_msg or "")[:200],
                    }],
                )
                result["vector"] = True
            except Exception as e:
                print(f"[Indexer] 向量写入失败: {e}")

        # 2. 关键词索引（先删后插，保证幂等）
        try:
            text = event.user_msg + " " + (event.ai_msg or "")
            with self._connect_fts() as con:
                con.execute("DELETE FROM fts_episodic WHERE id=?", (event.id,))
                con.execute("INSERT INTO fts_episodic(id, seg) VALUES(?,?)",
                            (event.id, seg(text)))
            result["lexical"] = True
        except Exception as e:
            print(f"[Indexer] 关键词写入失败: {e}")

        return result

    # ---------- 删除 ----------

    def delete(self, event_id: str):
        try:
            self.collection.delete(ids=[event_id])
        except Exception as e:
            print(f"[Indexer] 向量删除失败: {e}")
        try:
            with self._connect_fts() as con:
                con.execute("DELETE FROM fts_episodic WHERE id=?", (event_id,))
        except Exception as e:
            print(f"[Indexer] 关键词删除失败: {e}")

    # ---------- 统计 ----------

    def stats(self) -> dict:
        with self._connect_fts() as con:
            n_fts = con.execute("SELECT COUNT(*) FROM fts_episodic").fetchone()[0]
        return {"vector": self.collection.count(), "lexical": n_fts}