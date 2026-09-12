# backend/memory_retriever.py
# 职责：Shelwell 的长期记忆管理器
# 对外只暴露 add_memory / search 两个方法，内部实现可平滑升级

import os
import json
import uuid
import requests
from datetime import datetime
from pathlib import Path

import chromadb

from backend.config_loader import get

class MemoryRetriever:
    """
    Shelwell 的长期记忆管理器
    
    三大功能：
    1. 将每轮对话归档为 JSONL（原始数据，永不丢失）
    2. 将对话向量化后存入 ChromaDB（语义检索索引）
    3. 提供基于语义相似度的记忆检索
    
    设计原则：
    - JSONL 是"真相之源"，ChromaDB 只是索引
    - 所有检索调用都通过 search() 方法，未来可扩展为混合检索
    - 元数据预留扩展字段（entities/scene），为后续结构化升级铺路
    """
    
    def __init__(
        self,
        persist_dir: str = None,
        history_dir: str = None,
        ollama_url: str = "http://localhost:11434",
        embedding_model: str = "nomic-embed-text",
        collection_name: str = "shelwell_memory"
    ):
        # 路径处理（默认基于项目根目录）
        base_dir = Path(__file__).resolve().parent.parent
        self.persist_dir = persist_dir or str(base_dir / "memory" / "vector_store" / "chroma_db")
        self.history_dir = history_dir or str(base_dir / "memory" / "history")
        
        # 确保目录存在
        os.makedirs(self.persist_dir, exist_ok=True)
        os.makedirs(self.history_dir, exist_ok=True)
        
        # Ollama 配置
        self.ollama_url = (ollama_url or get("ollama.url", "http://localhost:11434")).rstrip("/")
        self.embedding_model = embedding_model or get("model.embedding", "nomic-embed-text")
        self.embedding_timeout = get("ollama.embedding_timeout", 30)
        
        # 初始化 ChromaDB（使用余弦距离，更适合语义检索）
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=collection_name or get("memory.collection_name", "shelwell_memory"),
            metadata={"hnsw:space": "cosine"}
        )
    
    # ============ 对外接口 ============
    
    def add_memory(self, user_msg: str, ai_msg: str, session_id: str = "default"):
        """
        将一轮对话写入长期记忆
        同时完成：JSONL 归档 + 向量化入库
        """
        timestamp = datetime.now().isoformat(timespec="seconds")
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        
        # 1. JSONL 归档（加保护）
        try:
            self._append_to_history({
                "id": memory_id,
                "timestamp": timestamp,
                "session_id": session_id,
                "user": user_msg,
                "assistant": ai_msg
            })
        except Exception as e:
            print(f"[Memory] JSONL归档失败: {e}")
        
        # 2. 向量化并存入 ChromaDB（已有 try 保护）
        combined_text = f"用户：{user_msg}\nShelwell：{ai_msg}"
        try:
            embedding = self._get_embedding(combined_text)
            self.collection.add(
                ids=[memory_id],
                embeddings=[embedding],
                documents=[combined_text],
                metadatas=[{
                    "timestamp": timestamp,
                    "session_id": session_id,
                    "user_msg": user_msg[:200],
                    "ai_msg": ai_msg[:200],
                    "type": "dialogue",
                    "entities": "",
                    "scene": ""
                }]
            )
            print(f"[Memory] 已写入记忆: {memory_id}")
        except Exception as e:
            print(f"[Memory] 向量化失败，已仅归档JSONL: {e}")
    
    def search(self, query: str, top_k: int = None, min_similarity: float = None):
        """
        检索与 query 相关的历史记忆
        返回: [{"content": ..., "metadata": ..., "similarity": ...}, ...]
        """
        if top_k is None:
            top_k = get("memory.top_k", 3)
        if min_similarity is None:
            min_similarity = get("memory.min_similarity", 0.35)

        if self.collection.count() == 0:
            return []
        
        try:
            query_embedding = self._get_embedding(query)
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, self.collection.count())
            )
        except Exception as e:
            print(f"[Memory] 检索失败: {e}")
            return []
        
        # 组装结果并过滤低相似度
        memories = []
        if results and results.get("documents"):
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            dists = results["distances"][0] if results.get("distances") else [0] * len(docs)
            
            for doc, meta, dist in zip(docs, metas, dists):
                similarity = 1 - dist   # 余弦距离转相似度
                if similarity >= min_similarity:
                    memories.append({
                        "content": doc,
                        "metadata": meta,
                        "similarity": round(similarity, 3)
                    })
        
        return memories
    
    def get_stats(self) -> dict:
        """返回记忆统计信息（调试用）"""
        return {
            "total_memories": self.collection.count(),
            "persist_dir": self.persist_dir,
            "history_dir": self.history_dir
        }
    
    # ============ 内部方法 ============
    
    def _get_embedding(self, text: str) -> list:
        """调用 Ollama 生成文本向量"""
        resp = requests.post(
            f"{self.ollama_url}/api/embeddings",
            json={"model": self.embedding_model, "prompt": text},
            timeout=self.embedding_timeout   # ★ 从配置读
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    
    def _append_to_history(self, record: dict):
        """将一条记录追加到当天的 JSONL 文件（原子写入）"""
        today = datetime.now().strftime("%Y-%m-%d")
        filepath = os.path.join(self.history_dir, f"{today}.jsonl")
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")