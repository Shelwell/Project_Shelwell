# backend/memory/semantic.py
# 职责：L3 语义记忆存储——事实表（SQLite 权威）+ 向量索引（ChromaDB）
# 原则：SQLite 是真相源，ChromaDB 可重建；事实支持更新、版本、来源追溯

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

import chromadb

from backend.memory.embedder import Embedder
from backend.memory.models import SemanticFact, FactState, now_iso


class SemanticStore:
    """语义事实存储"""

    def __init__(self, db_path: Optional[str] = None,
                 chroma_dir: Optional[str] = None,
                 collection_name: str = "shelwell_semantic"):
        base = Path(__file__).resolve().parent.parent.parent
        db_path = db_path or str(base / "memory" / "index" / "semantic.sqlite3")
        chroma_dir = chroma_dir or str(base / "memory" / "vector_store" / "chroma_db")

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

        self.client = chromadb.PersistentClient(path=chroma_dir)
        self.collection_name = collection_name
        self.collection = self.client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"})
        self.embedder = Embedder()

    def _ensure_collection(self):
        """集合被外部删除后句柄会失效，这里自动重建"""
        try:
            self.collection.count()
            return
        except Exception:
            pass
        try:
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name, metadata={"hnsw:space": "cosine"})
        except Exception as e:
            print(f"[Semantic] 重建 collection 失败: {e}")

    # ---------- SQLite ----------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_db(self):
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    fact TEXT NOT NULL,
                    subject TEXT DEFAULT '',
                    predicate TEXT DEFAULT '',
                    object TEXT DEFAULT '',
                    owner_id TEXT DEFAULT '',
                    owner_type TEXT DEFAULT 'user',
                    score REAL DEFAULT 0,
                    strength REAL DEFAULT 1.0,
                    confidence REAL DEFAULT 0.5,
                    detail_level REAL DEFAULT 1.0,
                    source_ids TEXT DEFAULT '[]',
                    valid_from TEXT DEFAULT '',
                    valid_to TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT '',
                    last_activated_at TEXT DEFAULT '',
                    state TEXT DEFAULT 'active',
                    extractor_version TEXT DEFAULT 'v1',
                    tag TEXT DEFAULT 'misc',
                    hits INTEGER DEFAULT 0,
                    base_weight REAL DEFAULT 0,
                    archived INTEGER DEFAULT 0,
                    last_hit TEXT DEFAULT ''
                )
            """)
            # 旧库兼容：缺列则补
            cols = {r[1] for r in con.execute("PRAGMA table_info(facts)").fetchall()}
            for col, ddl in [
                ("tag", "ALTER TABLE facts ADD COLUMN tag TEXT DEFAULT 'misc'"),
                ("hits", "ALTER TABLE facts ADD COLUMN hits INTEGER DEFAULT 0"),
                ("base_weight", "ALTER TABLE facts ADD COLUMN base_weight REAL DEFAULT 0"),
                ("archived", "ALTER TABLE facts ADD COLUMN archived INTEGER DEFAULT 0"),
                ("last_hit", "ALTER TABLE facts ADD COLUMN last_hit TEXT DEFAULT ''"),
            ]:
                if col not in cols:
                    con.execute(ddl)
            con.execute("CREATE INDEX IF NOT EXISTS idx_facts_owner ON facts(owner_type, owner_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_facts_state ON facts(state)")

    # ---------- 写入 ----------

    def add(self, fact: SemanticFact, owner_id: str = "", owner_type: str = "user") -> bool:
        """新增一条语义事实，同时写 SQLite 和向量索引"""
        try:
            with self._connect() as con:
                con.execute("""
                    INSERT INTO facts(id,fact,subject,predicate,object,owner_id,owner_type,
                        score,strength,confidence,detail_level,source_ids,valid_from,valid_to,
                        created_at,updated_at,last_activated_at,state,extractor_version,
                        tag,hits,base_weight,archived,last_hit)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    fact.id, fact.fact, fact.subject, fact.predicate, fact.object,
                    owner_id, owner_type, fact.score, fact.strength, fact.confidence,
                    fact.detail_level, json.dumps(fact.source_ids, ensure_ascii=False),
                    fact.valid_from, fact.valid_to, fact.created_at, fact.updated_at,
                    fact.last_activated_at, fact.state, fact.extractor_version,
                    getattr(fact, "tag", "misc"), getattr(fact, "hits", 0),
                    getattr(fact, "base_weight", 0), 1 if getattr(fact, "archived", False) else 0,
                    getattr(fact, "last_hit", ""),
                ))
        except Exception as e:
            print(f"[Semantic] SQLite 写入失败: {e}")
            return False

        self._index_vector(fact)
        return True

    def update(self, fact_id: str, patch: dict) -> bool:
        """更新事实字段（含来源合并、置信度、强度）"""
        if not patch:
            return False
        fields = []
        values = []
        for k, v in patch.items():
            if k == "source_ids":
                fields.append("source_ids=?")
                values.append(json.dumps(v, ensure_ascii=False))
            else:
                fields.append(f"{k}=?")
                values.append(v)
        fields.append("updated_at=?")
        values.append(now_iso())
        values.append(fact_id)
        try:
            with self._connect() as con:
                con.execute(f"UPDATE facts SET {','.join(fields)} WHERE id=?", values)
        except Exception as e:
            print(f"[Semantic] 更新失败: {e}")
            return False

        # 同步向量（fact 文本可能变）
        row = self.get(fact_id)
        if row:
            self._index_vector(row)
        return True

    def _index_vector(self, fact: SemanticFact):
        self._ensure_collection()
        """把事实文本向量化写入 ChromaDB"""
        vec = self.embedder.embed_safe(fact.fact)
        if not vec:
            return
        try:
            self.collection.upsert(
                ids=[fact.id],
                embeddings=[vec],
                documents=[fact.fact],
                metadatas=[{
                    "state": fact.state,
                    "strength": fact.strength,
                    "confidence": fact.confidence,
                }],
            )
        except Exception as e:
            print(f"[Semantic] 向量索引失败: {e}")

    # ---------- 查询 ----------

    def get(self, fact_id: str) -> Optional[SemanticFact]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        return self._row_to_fact(row) if row else None

    def _row_to_fact(self, row) -> SemanticFact:
        d = dict(row)
        d["source_ids"] = json.loads(d.get("source_ids") or "[]")
        if "archived" in d:
            d["archived"] = bool(d["archived"])
        known = {f.name for f in SemanticFact.__dataclass_fields__.values()}
        return SemanticFact(**{k: v for k, v in d.items() if k in known})

    def list_facts(self, page: int = 1, page_size: int = 20,
                   owner_id: str = "", state: str = "",
                   keyword: str = "") -> dict:
        """分页列出事实"""
        where = []
        params = []
        if owner_id:
            where.append("owner_id=?")
            params.append(owner_id)
        if state:
            where.append("state=?")
            params.append(state)
        if keyword:
            where.append("(fact LIKE ? OR subject LIKE ? OR object LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        clause = ("WHERE " + " AND ".join(where)) if where else ""

        with self._connect() as con:
            total = con.execute(f"SELECT COUNT(*) FROM facts {clause}", params).fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            page = max(1, min(page, pages))
            offset = (page - 1) * page_size
            rows = con.execute(
                f"SELECT * FROM facts {clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset]).fetchall()
        return {
            "items": [self._row_to_fact(r) for r in rows],
            "total": total, "page": page, "page_size": page_size, "pages": pages,
        }

    def search(self, query: str, top_k: int = 5,
               min_similarity: float = 0.4, owner_id: str = "") -> list:
        self._ensure_collection()
        """
        检索语义事实：向量相似度 × strength 加权排序。
        owner_id 为空时不过滤 owner。
        """
        if self.collection.count() == 0:
            return []
        vec = self.embedder.embed_safe(query)
        if not vec:
            return []
        try:
            where = {"state": FactState.ACTIVE.value}
            qres = self.collection.query(
                query_embeddings=[vec],
                n_results=min(top_k * 3, self.collection.count()),
                where=where,
            )
        except Exception as e:
            print(f"[Semantic] 检索失败: {e}")
            return []

        hits = []
        if qres and qres.get("ids"):
            for fid, dist in zip(qres["ids"][0], qres["distances"][0]):
                sim = 1 - dist
                if sim < min_similarity:
                    continue
                fact = self.get(fid)
                if not fact:
                    continue
                if owner_id:
                    # 二次过滤 owner
                    with self._connect() as con:
                        r = con.execute("SELECT owner_id FROM facts WHERE id=?", (fid,)).fetchone()
                    if r and r["owner_id"] != owner_id:
                        continue
                # 过滤已归档
                if getattr(fact, "archived", False):
                    continue
                # 权重归一化：base_weight 最高 3.0，加上命中回补最多 4.5，衰减后可能更低
                from backend.memory.forgetting import weight_of, TAG_WEIGHTS
                w = weight_of(fact)
                # 映射到 0.3~1.0 区间参与排序（避免权重过低直接归零）
                w_norm = min(1.0, max(0.3, w / 3.0))
                rank = sim * w_norm
                hits.append({"fact": fact, "similarity": round(sim, 3),
                             "weight": round(w, 2), "rank": round(rank, 3)})
        hits.sort(key=lambda x: x["rank"], reverse=True)
        return hits[:top_k]

    # ---------- 维护 ----------

    def reap_archived(self) -> int:
        """批量把权重跌破归档线的事实标记为 archived（不删除），返回归档数"""
        from backend.memory.forgetting import weight_of, ARCHIVE_THRESHOLD
        n = 0
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM facts WHERE archived=0 AND state='active'").fetchall()
            for r in rows:
                fact = self.get(r["id"])
                if fact and weight_of(fact) < ARCHIVE_THRESHOLD:
                    con.execute("UPDATE facts SET archived=1, updated_at=? WHERE id=?",
                                (now_iso(), r["id"]))
                    n += 1
        return n

    def delete(self, fact_id: str) -> bool:
        try:
            with self._connect() as con:
                con.execute("DELETE FROM facts WHERE id=?", (fact_id,))
            self.collection.delete(ids=[fact_id])
            return True
        except Exception as e:
            print(f"[Semantic] 删除失败: {e}")
            return False

    def detach_source(self, event_id: str) -> int:
        """
        从所有事实的 source_ids 中摘掉指定情景 ID（删除情景记忆时调用）。
        事实若因此没有任何来源，则连同向量一并删除。返回受影响的事实数。
        """
        n = 0
        with self._connect() as con:
            rows = con.execute("SELECT id, source_ids FROM facts").fetchall()
            for r in rows:
                try:
                    ids = json.loads(r["source_ids"] or "[]")
                except Exception:
                    continue
                if event_id not in ids:
                    continue
                keep = [x for x in ids if x != event_id]
                if keep:
                    con.execute(
                        "UPDATE facts SET source_ids=?, updated_at=? WHERE id=?",
                        (json.dumps(keep, ensure_ascii=False), now_iso(), r["id"]))
                else:
                    con.execute("DELETE FROM facts WHERE id=?", (r["id"],))
                    try:
                        self.collection.delete(ids=[r["id"]])
                    except Exception:
                        pass
                n += 1
        return n

    def clear_all(self) -> int:
        with self._connect() as con:
            n = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            con.execute("DELETE FROM facts")
        try:
            all_ids = [r for r in self.collection.get(include=[])["ids"]]
            if all_ids:
                self.collection.delete(ids=all_ids)
        except Exception:
            pass
        return n

    def stats(self) -> dict:
        self._ensure_collection()
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            by_state = {r[0]: r[1] for r in con.execute(
                "SELECT state, COUNT(*) FROM facts GROUP BY state").fetchall()}
        with self._connect() as con:
            archived = con.execute("SELECT COUNT(*) FROM facts WHERE archived=1").fetchone()[0]
        return {"total": total, "by_state": by_state,
                "archived": archived, "vector": self.collection.count()}