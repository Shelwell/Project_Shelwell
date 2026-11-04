# backend/memory_retriever.py
# 职责：长期记忆管理器（对外兼容层）
# 说明：阶段 A 起，内部实现委托给 backend.memory.service.MemoryService
#       对外签名保持 add_memory / search / get_stats 不变，调用方无需修改

from backend.config_loader import get
from backend.memory.service import MemoryService


class MemoryRetriever:
    """
    西尔维娅的长期记忆管理器

    存储分两层：
    1. 情景记忆：JSONL 权威归档 + ChromaDB 向量索引 + SQLite FTS5 关键词索引
    2. 语义记忆：后续阶段接入

    设计原则：
    - JSONL 是真相之源，索引可重建
    - 写入异步建索引，不阻塞对话
    - 对外接口保持不变
    """

    def __init__(self,
                 persist_dir: str = None,
                 history_dir: str = None,
                 ollama_url: str = None,
                 embedding_model: str = None,
                 collection_name: str = None,
                 auto_worker: bool = True):
        # 保留旧构造参数以兼容调用方，实际路径交由 MemoryService 决定
        self._svc = MemoryService(auto_worker=auto_worker)
        if auto_worker:
            # 启动后立即处理一次积压任务
            try:
                self._svc.run_once("index")
            except Exception as e:
                print(f"[Memory] 启动处理积压任务失败: {e}")

    # ============ 对外接口 ============

    @property
    def relationship(self):
        """关系等级存储（由 MemoryService 持有，随归档消息自动累加）"""
        return getattr(self._svc, "relationship", None)

    def add_memory(self, user_msg: str, ai_msg: str, session_id: str = "default",
                   speaker_id: str = "", channel_type: str = "",
                   channel_id: str = "", speaker_name: str = ""):
        """将一轮对话写入长期记忆（归档 + 同步索引）"""
        return self._svc.add_memory(
            user_msg, ai_msg, session_id=session_id,
            speaker_id=speaker_id, channel_type=channel_type, channel_id=channel_id,
            speaker_name=speaker_name,
        )

    def search(self, query: str, top_k: int = None, min_similarity: float = None,
               session_id: str = None, speaker_id: str = None,
               channel_type: str = None, channel_id: str = None):
        """
        检索与 query 相关的历史记忆
        返回: [{"content": ..., "metadata": ..., "similarity": ...}, ...]
        """
        return self._svc.search(query, top_k=top_k,
                                min_similarity=min_similarity,
                                session_id=session_id,
                                speaker_id=speaker_id,
                                channel_type=channel_type,
                                channel_id=channel_id)

    def get_stats(self) -> dict:
        """返回记忆统计信息（兼容旧字段 total_memories）"""
        s = self._svc.get_stats()
        return {
            "total_memories": s["archive"]["total"],
            "archive": s["archive"],
            "index": s["index"],
            "jobs": s["jobs"],
        }

    def get_group_context(self, channel_id: str, limit: int = 15,
                          bot_name: str = "她") -> list:
        """拉取群聊最近 N 条消息作为上下文"""
        return self._svc.get_group_context(channel_id, limit=limit,
                                           bot_name=bot_name)

    # ============ 语义记忆 ============

    def search_semantic(self, query: str, top_k: int = 5,
                        min_similarity: float = 0.4, owner_id: str = "") -> list:
        return self._svc.search_semantic(query, top_k=top_k,
                                         min_similarity=min_similarity,
                                         owner_id=owner_id)

    def consolidate(self, session_filter: str = None, limit: int = 50,
                    dry_run: bool = False) -> dict:
        return self._svc.consolidate(session_filter=session_filter,
                                     limit=limit, dry_run=dry_run)

    def list_semantic_facts(self, page: int = 1, page_size: int = 20,
                            owner_id: str = "", state: str = "",
                            keyword: str = "") -> dict:
        return self._svc.list_semantic_facts(page, page_size, owner_id, state, keyword)

    def delete_semantic_fact(self, fact_id: str) -> bool:
        return self._svc.delete_semantic_fact(fact_id)

    def clear_semantic(self) -> int:
        return self._svc.clear_semantic()

    # ============ 扩展能力 ============

    def import_jsonl(self, path: str, session_override: str = None) -> dict:
        """从外部 JSONL 导入记忆"""
        return self._svc.import_jsonl(path, session_override=session_override)

    def delete_event(self, event_id: str) -> bool:
        """删除单条记忆"""
        return self._svc.delete_event(event_id)

    def delete_session(self, session_id: str) -> int:
        """删除某会话全部记忆"""
        return self._svc.delete_session(session_id)

    def clear_all(self) -> dict:
        """清空全部记忆"""
        return self._svc.clear_all()

    def shutdown(self):
        """停止后台线程"""
        try:
            self._svc.stop_worker()
        except Exception:
            pass