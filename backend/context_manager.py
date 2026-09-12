# backend/context_manager.py
from backend.memory_retriever import MemoryRetriever
from backend.config_loader import get   


class ContextManager:
    """
    对话上下文管理器
    三级优先级：
    1. 最近 N 条短期上下文（始终携带）
    2. 从长期记忆库检索相关历史（仅在相关时注入）
    3. 若以上均无 → 由模型根据自身人格生成回复
    """

    # 系统指令模板（含多气泡格式要求）
    SYSTEM_INSTRUCTION = (
        "你是 Shelwell，正在和一个你熟悉的朋友聊天。\n\n"
        "【说话风格】\n"
        "- 像真人发微信一样，把回复拆成2-4条短消息\n"
        "- 每条消息不超过25个字\n"
        "- 每条短消息单独占一行，直接换行\n"
        "- 短句之间要有递进、转折或留白，不要机械切分\n"
        "- 自然口语，不要客套话，不要总结式收尾\n"
        "- 陈述自己观点时用陈述句（如“我觉得……”），不要用问句收尾\n\n"
        "【输出格式——非常重要】\n"
        "- 你的回复就是你对朋友说的话本身\n"
        "- 不要写“用户：”“你：”“Shelwell：”这类角色标签\n"
        "- 不要模拟用户说话，不要自问自答\n"
        "- 只输出你自己的回复内容\n"
        "- 不要输出 \\n 这两个字符，要换行就直接换行\n\n"
        "【什么时候该拆】\n"
        "- 日常闲聊、情绪陪伴、吐槽：拆成2-4条\n"
        "- 技术问题、严肃讨论：可以只回1-2条，不必强行拆\n\n"
        "【禁止】\n"
        "- 不用表情符号\n"
        "- 不用项目符号（如 · 或 -）罗列选项\n"
        "- 不用“首先/其次/总之/希望对你有帮助”这类套话"
    )

    def __init__(self, max_context_length: int = None, memory_enabled: bool = None):
        self.max_context_length = max_context_length if max_context_length is not None \
            else get("context.max_length", 30)
        self.memory_enabled = memory_enabled if memory_enabled is not None \
            else get("context.memory_enabled", True)

        try:
            self.memory = MemoryRetriever() if self.memory_enabled else None
        except Exception as e:
            print(f"[Context] MemoryRetriever 初始化失败，降级为无记忆模式: {e}")
            self.memory = None
            self.memory_enabled = False

    def prepare_context(self, messages: list, current_query: str = None) -> list:
        """
        构建发送给模型的消息列表
        兼容 Mistral 等 [INST] 格式模型：不单独发 system 消息，
        而是把系统指令合并到第一条 user 消息的开头
        """
        if not messages:
            messages = []

        # --- 第一级：最近 N 条短期上下文 ---
        recent = messages[-self.max_context_length:].copy() if messages else []

        # --- 收集所有 system 内容 ---
        system_parts = [self.SYSTEM_INSTRUCTION]

        # --- 第二级：长期记忆检索 ---
        if self.memory_enabled and self.memory is not None and current_query:
            try:
                memories = self.memory.search(current_query, top_k=3)
            except Exception as e:
                print(f"[Context] 记忆检索异常: {e}")
                memories = []

            if memories:
                try:
                    memory_text = self._format_memories(memories)
                    system_parts.append(
                        f"【相关历史记忆（供参考，不必刻意提及）】\n{memory_text}"
                    )
                    print(f"[Context] 注入 {len(memories)} 条长期记忆")
                except Exception as e:
                    print(f"[Context] 记忆格式化失败: {e}")

        combined_system = "\n\n".join(system_parts)

        # --- 关键改动：把系统指令合并到第一条 user 消息里，不单独发 system 消息 ---
        if recent and recent[0]["role"] == "user":
            recent[0] = {
                "role": "user",
                "content": f"{combined_system}\n\n---\n\n{recent[0]['content']}"
            }
        else:
            # 如果没有 user 消息（异常情况），构造一条
            recent.insert(0, {
                "role": "user",
                "content": combined_system
            })

        return recent

    def archive_turn(self, user_msg: str, ai_msg: str, session_id: str = "default"):
        """归档一轮对话到长期记忆（保留完整内容含 ||| 分隔符）"""
        if self.memory_enabled and self.memory is not None:
            try:
                self.memory.add_memory(user_msg, ai_msg, session_id)
            except Exception as e:
                print(f"[Context] 归档失败: {e}")

    def get_stats(self, messages: list) -> dict:
        """获取当前对话与记忆的统计信息"""
        total = len(messages) if messages else 0
        used = min(total, self.max_context_length)
        mem_total = 0
        if self.memory_enabled and self.memory is not None:
            try:
                mem_total = self.memory.get_stats().get("total_memories", 0)
            except Exception:
                mem_total = 0
        return {
            "total_messages": total,
            "context_used": used,
            "max_allowed": self.max_context_length,
            "total_memories": mem_total
        }

    def _format_memories(self, memories: list) -> str:
        """将检索到的记忆格式化为可读文本"""
        lines = []
        for m in memories:
            ts = m.get("metadata", {}).get("timestamp", "")[:10]
            content = m.get("content", "").replace("|||", "\n")
            lines.append(f"[{ts}] {content}")
        return "\n\n".join(lines)
