# backend/memory/embedder.py
# 职责：L3 记忆系统的文本向量化封装
# 说明：当前直连 Ollama embeddings 接口；待 L2 provider 支持嵌入后下沉

import requests

from backend.config_loader import get


class Embedder:
    """文本向量化"""

    def __init__(self, ollama_url: str = None, model: str = None, timeout: int = None):
        self.url = (ollama_url or get("ollama.url", "http://localhost:11434")).rstrip("/")
        self.model = model or get("model.embedding", "nomic-embed-text")
        self.timeout = timeout or get("ollama.embedding_timeout", 30)

    def embed(self, text: str) -> list:
        """单条文本向量化，失败抛异常"""
        resp = requests.post(
            f"{self.url}/api/embeddings",
            json={
                "model": self.model,
                "prompt": text,
                # num_gpu=0：强制 CPU 推理，不占显存，与 12B 生成模型零冲突
                # keep_alive=30m：CPU 加载需 3.7s，常驻内存比反复加载划算；
                # CPU 内存占用约 700MB，不挤占显存
                "keep_alive": "30m",
                "options": {"num_gpu": 0},
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    def embed_safe(self, text: str):
        """安全版：失败返回 None，不抛异常"""
        try:
            return self.embed(text)
        except Exception as e:
            print(f"[Embedder] 向量化失败: {e}")
            return None