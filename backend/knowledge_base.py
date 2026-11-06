# backend/knowledge_base.py
# 职责：L3 能力层——长期 RAG 知识库（客观世界/背景设定，公有、静态、事实）
# 边界：与对话记忆严格分离
#   记忆库 = "你与我"（私密、动态、关系、经历）
#   知识库 = "世界"  （公有、静态、设定、事实）
# 两者使用独立 collection，检索结果各自打标签，互不校正、互不竞争排序

import json
import os
import re
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
                 embedder=None, jsonl_path: Optional[str] = None):
        base = Path(__file__).resolve().parent.parent
        chroma_dir = chroma_dir or str(base / "memory" / "vector_store" / "chroma_db")
        os.makedirs(chroma_dir, exist_ok=True)

        self.client = chromadb.PersistentClient(path=chroma_dir)
        self.collection_name = collection_name
        self.collection = self.client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"})
        self.embedder = embedder or Embedder()
        jp = Path(jsonl_path) if jsonl_path else (
            base / "config" / "persona_prompts" / "shelwell_knowledge.jsonl")
        self.jsonl_path = jp if jp.is_absolute() else base / jp

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

    # ---------- JSONL CRUD ----------

    @staticmethod
    def _norm_list(value) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        text = str(value).replace("，", ",")
        out = []
        for line in text.splitlines():
            out.extend(x.strip() for x in line.split(",") if x.strip())
        return out

    @staticmethod
    def _build_embed_text(item: dict) -> str:
        parts = [
            str(item.get("title", "") or "").strip(),
            " ".join(KnowledgeBase._norm_list(item.get("aliases"))),
            " ".join(KnowledgeBase._norm_list(item.get("questions"))),
            str(item.get("content", "") or "").strip(),
        ]
        return " ".join(p for p in parts if p)

    def _read_jsonl(self) -> list:
        path = self.jsonl_path
        if not path.exists():
            return []
        items = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    items.append(obj)
        return items

    def _write_jsonl(self, items: list) -> None:
        path = self.jsonl_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline=chr(10)) as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + chr(10))
        tmp.replace(path)

    def _normalize_item(self, item: dict) -> dict:
        if not isinstance(item, dict):
            raise ValueError("知识条目必须是对象")
        out = dict(item)
        out["id"] = str(out.get("id", "") or "").strip()
        out["title"] = str(out.get("title", "") or "").strip()
        out["category"] = str(out.get("category", "") or "").strip() or "other"
        out["content"] = str(out.get("content", "") or "").strip()
        out["aliases"] = self._norm_list(out.get("aliases"))
        out["questions"] = self._norm_list(out.get("questions"))
        out["related"] = self._norm_list(out.get("related"))
        out["canon"] = str(out.get("canon", "") or "").strip() or "原文"
        out["source"] = str(out.get("source", "") or "").strip() or "desktop_ui"
        if not out["id"] or not out["title"] or not out["content"]:
            raise ValueError("id、title、content 不能为空")
        out["embed_text"] = self._build_embed_text(out)
        return out

    def next_id(self) -> str:
        nums = []
        for item in self._read_jsonl():
            sid = str(item.get("id", ""))
            if sid[:1].upper() == "K" and sid[1:].isdigit():
                nums.append(int(sid[1:]))
        return "K%03d" % (max(nums, default=0) + 1)

    def list_items(self, keyword: str = "", category: str = "",
                   page: int = 1, page_size: int = 20) -> dict:
        all_items = self._read_jsonl()
        items = list(all_items)
        kw = (keyword or "").strip().lower()
        cat = (category or "").strip()
        if cat:
            items = [x for x in items if str(x.get("category", "")) == cat]
        if kw:
            def hit(x):
                blob = " ".join([
                    str(x.get("id", "")), str(x.get("title", "")),
                    str(x.get("category", "")), str(x.get("content", "")),
                    " ".join(x.get("aliases") or []),
                    " ".join(x.get("questions") or []),
                ]).lower()
                return kw in blob
            items = [x for x in items if hit(x)]
        items.sort(key=lambda x: str(x.get("id", "")))
        total = len(items)
        page = max(1, int(page or 1))
        page_size = max(1, min(200, int(page_size or 20)))
        pages = max(1, (total + page_size - 1) // page_size)
        if page > pages:
            page = pages
        start = (page - 1) * page_size
        cats = sorted({str(x.get("category", "")) for x in all_items
                       if str(x.get("category", "")).strip()})
        return {"items": items[start:start + page_size], "total": total,
                "page": page, "page_size": page_size, "pages": pages,
                "categories": cats, "jsonl": str(self.jsonl_path)}

    def get_item(self, item_id: str) -> dict:
        item_id = str(item_id or "").strip()
        for item in self._read_jsonl():
            if str(item.get("id", "")) == item_id:
                return item
        return {}

    def upsert_item(self, item: dict) -> dict:
        out = self._normalize_item(item)
        items = self._read_jsonl()
        replaced = False
        for i, old in enumerate(items):
            if str(old.get("id", "")) == out["id"]:
                items[i] = out
                replaced = True
                break
        if not replaced:
            items.append(out)
        self._write_jsonl(items)
        r = self.ingest([out])
        if not r.get("ok"):
            raise RuntimeError("向量入库失败，JSONL 已写入，可重建索引")
        return {"item": out, "created": not replaced,
                "ingest": r, "jsonl": str(self.jsonl_path)}

    def delete_item(self, item_id: str) -> dict:
        item_id = str(item_id or "").strip()
        items = self._read_jsonl()
        new_items = [x for x in items if str(x.get("id", "")) != item_id]
        if len(new_items) == len(items):
            return {"ok": False, "error": "知识不存在"}
        self._write_jsonl(new_items)
        try:
            self._ensure_collection()
            self.collection.delete(ids=[item_id])
        except Exception as e:
            return {"ok": False, "error": "JSONL 已删除，Chroma 删除失败: %s" % e}
        return {"ok": True, "id": item_id, "jsonl": str(self.jsonl_path)}

    def import_jsonl(self, path: str, merge: bool = True) -> dict:
        src = Path(path)
        if not src.exists():
            return {"ok": False, "error": "文件不存在"}
        incoming = []
        with open(src, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    incoming.append(json.loads(line))
                except Exception:
                    continue
        existing = {} if not merge else {
            str(x.get("id", "")): x for x in self._read_jsonl()}
        for it in incoming:
            try:
                out = self._normalize_item(it)
            except Exception:
                continue
            existing[out["id"]] = out
        items = sorted(existing.values(), key=lambda x: str(x.get("id", "")))
        self._write_jsonl(items)
        r = self.ingest(items, rebuild=False)
        return {"ok": True, "imported": len(incoming), "total": len(items),
                "ingest": r, "jsonl": str(self.jsonl_path)}

    def rebuild_index(self) -> dict:
        items = self._read_jsonl()
        return self.ingest(items, rebuild=True)

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
    jp = cfg.get("path") or "config/persona_prompts/shelwell_knowledge.jsonl"
    p = Path(jp)
    if not p.is_absolute():
        p = base / p
    return KnowledgeBase(
        chroma_dir=str(base / "memory" / "vector_store" / "chroma_db"),
        collection_name=cfg.get("collection_name", "shelwell_knowledge"),
        jsonl_path=str(p),
    )
