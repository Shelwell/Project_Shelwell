# backend/knowledge_base.py
# 职责：L3 能力层——长期 RAG 知识库（客观世界/背景设定，公有、静态、事实）
# 边界：与对话记忆严格分离
#   记忆库 = "你与我"（私密、动态、关系、经历）
#   知识库 = "世界"  （公有、静态、设定、事实）
# 两者使用独立 collection，检索结果各自打标签，互不校正、互不竞争排序

import json
import os
from pathlib import Path
from typing import Optional

import chromadb

from backend.memory.embedder import Embedder

DEFAULTS = {
    "enabled": True,
    "collection_name": "shelwell_knowledge",
    "top_k": 2,
    "min_similarity": 0.55,
}


class KnowledgeBase:
    """废铁城档案库：JSONL → ChromaDB，按查询检索 Top-K 客观设定"""

    def __init__(self, chroma_dir: Optional[str] = None,
                 collection_name: str = "shelwell_knowledge",
                 embedder=None):
        base = Path(__file__).resolve().parent.parent
        chroma_dir = chroma_dir or str(base / "memory" / "vector_store" / "chroma_db")
        os.makedirs(chroma_dir, exist_ok=True)

        self.client = chromadb.PersistentClient(path=chroma_dir)
        self.collection_name = collection_name
        self.collection = self.client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"})
        self.embedder = embedder or Embedder()

    # ---------- 内部 ----------

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
            print(f"[Knowledge] 重建 collection 失败: {e}")

    @staticmethod
    def _meta(item: dict) -> dict:
        """Chroma metadata 只接受标量，列表统一转字符串"""
        return {
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "category": item.get("category", ""),
            "canon": item.get("canon", ""),
            "aliases": "、".join(item.get("aliases") or []),
            "questions": " / ".join(item.get("questions") or []),
            "content": item.get("content", ""),
        }

    # ---------- 入库 ----------

    def ingest(self, items: list, rebuild: bool = False) -> dict:
        """
        批量入库（按 id 幂等覆盖）。rebuild=True 时先清空重建。
        返回 {"total": n, "ok": n, "skipped": n, "rebuild": bool}
        """
        if rebuild:
            try:
                self.client.delete_collection(self.collection_name)
            except Exception as e:
                print(f"[Knowledge] 删除旧集合失败（可忽略）: {e}")
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name, metadata={"hnsw:space": "cosine"})
        self._ensure_collection()

        ok = skipped = 0
        batch_ids, batch_emb, batch_docs, batch_meta = [], [], [], []

        def flush():
            nonlocal ok
            if not batch_ids:
                return
            try:
                self.collection.upsert(
                    ids=list(batch_ids), embeddings=list(batch_emb),
                    documents=list(batch_docs), metadatas=list(batch_meta))
                ok += len(batch_ids)
            except Exception as e:
                print(f"[Knowledge] 写入失败: {e}")
            batch_ids.clear(); batch_emb.clear()
            batch_docs.clear(); batch_meta.clear()

        for it in items:
            text = it.get("embed_text") or it.get("content") or ""
            if not it.get("id") or not text:
                skipped += 1
                continue
            vec = self.embedder.embed_safe(text)
            if not vec:
                skipped += 1
                continue
            batch_ids.append(it["id"])
            batch_emb.append(vec)
            batch_docs.append(text)
            batch_meta.append(self._meta(it))
            if len(batch_ids) >= 16:
                flush()
        flush()
        return {"total": len(items), "ok": ok, "skipped": skipped, "rebuild": rebuild}

    def ingest_jsonl(self, path: str, rebuild: bool = False) -> dict:
        items = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return self.ingest(items, rebuild=rebuild)

    # ---------- 检索 ----------

    def search(self, query: str, top_k: int = 2,
               min_similarity: float = 0.55) -> list:
        """
        检索客观设定。返回 [{"id","title","category","content","similarity"}]
        向量相似度不足 min_similarity 的丢弃——宁可查不到，不要塞无关设定。
        """
        self._ensure_collection()
        if not query or self.collection.count() == 0:
            return []
        vec = self.embedder.embed_safe(query)
        if not vec:
            return []
        try:
            qres = self.collection.query(
                query_embeddings=[vec],
                n_results=min(max(top_k * 3, 3), self.collection.count()))
        except Exception as e:
            print(f"[Knowledge] 检索失败: {e}")
            return []

        out = []
        ids = (qres or {}).get("ids", [[]])[0]
        dists = (qres or {}).get("distances", [[]])[0]
        metas = (qres or {}).get("metadatas", [[]])[0]
        for fid, dist, meta in zip(ids, dists, metas):
            sim = 1.0 - float(dist)
            if sim < min_similarity:
                continue
            meta = meta or {}
            out.append({
                "id": fid,
                "title": meta.get("title", ""),
                "category": meta.get("category", ""),
                "content": meta.get("content", ""),
                "similarity": round(sim, 3),
            })
        out.sort(key=lambda x: x["similarity"], reverse=True)
        return out[:top_k]

    def stats(self) -> dict:
        self._ensure_collection()
        try:
            return {"collection": self.collection_name,
                    "count": self.collection.count()}
        except Exception as e:
            return {"collection": self.collection_name, "count": 0, "error": str(e)}


def build_from_config(project_root: str = None) -> KnowledgeBase:
    """按 app_config 的 knowledge 段创建实例（供 context_manager 使用）"""
    from backend.config_loader import get
    cfg = get("knowledge", {}) or {}
    base = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    return KnowledgeBase(
        chroma_dir=str(base / "memory" / "vector_store" / "chroma_db"),
        collection_name=cfg.get("collection_name", "shelwell_knowledge"),
    )
