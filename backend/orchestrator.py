# backend/orchestrator.py
# 职责：L4 编排层——一次对话的完整流程
# 收消息 → 建上下文 → 调模型 → 清洗回复 → 归档 → 返回

from backend.config_loader import get
from backend.context_manager import ContextManager
from backend.llm import provider


class Orchestrator:
    """
    对话编排器（无状态服务）

    多会话隔离：调用方传入 session_id，orchestrator 本身不持有对话历史。
    历史由调用方维护，每次调用传入完整 messages 列表。
    """

    def __init__(self, context_manager: ContextManager = None):
        self.context = context_manager or ContextManager(
            max_context_length=get("context.max_length", 30),
            memory_enabled=get("context.memory_enabled", True),
        )

    def chat(self, messages: list, session_id: str = "default", current_query: str = None) -> dict:
        """
        执行一次完整对话。

        参数:
            messages: 完整对话历史 [{"role": "user"/"assistant", "content": "..."}]
            session_id: 会话标识，用于记忆隔离
            current_query: 当前用户输入；为 None 时从 messages 末尾自动取

        返回:
            {"reply": str, "ok": bool, "error": str|None}
        """
        if not messages:
            return {"reply": "", "ok": False, "error": "empty_messages"}

        if current_query is None:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    current_query = msg["content"]
                    break

        # 1. 构建上下文（短期 + 长期记忆 + 人格指令）
        try:
            try:
                from backend.runtime_config import get_runtime
                _prov = (get_runtime().get("active.provider") or "ollama").strip()
            except Exception:
                _prov = "ollama"
            prepared = self.context.prepare_context(
                messages, current_query=current_query, session_id=session_id,
                system_as_role=(_prov == "openai_compatible"),
            )
        except Exception as e:
            print(f"[Orchestrator] 上下文构建失败，降级为纯上下文: {e}")
            prepared = messages[-get("context.max_length", 30):]

        # 2. 调模型
        try:
            raw_reply = self._call_model(prepared)
        except Exception as e:
            return {"reply": "", "ok": False, "error": f"model_error: {e}"}

        # 3. 清洗
        reply = self._clean_reply(raw_reply)
        if not reply:
            return {"reply": "", "ok": False, "error": "empty_reply"}

        # 4. 归档
        if current_query:
            try:
                self.context.archive_turn(
                    user_msg=current_query, ai_msg=reply, session_id=session_id
                )
            except Exception as e:
                print(f"[Orchestrator] 归档失败（不影响对话）: {e}")

        return {"reply": reply, "ok": True, "error": None}

    def _call_model(self, prepared_messages: list) -> str:
        """调用 L2 provider，返回原始文本"""
        return provider.chat(prepared_messages)

    def _clean_reply(self, text: str) -> str:
        """清洗：字面 \\n 转换、去角色标签、去首尾空白"""
        if not text:
            return ""
        text = text.replace("\\n", "\n")
        tags = ["用户：", "用户:", "Shelwell：", "Shelwell:", "你：", "你:"]
        cleaned = []
        for line in text.split("\n"):
            for tag in tags:
                if line.startswith(tag):
                    line = line[len(tag):]
                    break
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    def get_stats(self, messages: list) -> dict:
        """透传 ContextManager 的统计信息"""
        try:
            return self.context.get_stats(messages)
        except Exception:
            return {}