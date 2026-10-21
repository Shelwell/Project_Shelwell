# backend/context_manager.py
from backend.memory_retriever import MemoryRetriever
from backend.config_loader import get
from backend.persona_engine import PersonaEngine


class ContextManager:
    """
    对话上下文管理器
    三级优先级：
    1. 最近 N 条短期上下文（始终携带）
    2. 从长期记忆库检索相关历史（仅在相关时注入）
    3. 若以上均无 → 由模型根据自身人格生成回复
    """

    # 输出格式规范（运行时与人格提示词拼接）
    OUTPUT_FORMAT = (
        "你是 Shelwell，正在和一个你熟悉的朋友聊天。\n\n"
        "【怎么说话——最重要】\n"
        "- 像真人发微信。想发一条就发一条，想拆两条就拆两条，想连发几条也行，看内容和情绪自然决定\n"
        "- 不要求每条回复都拆开。很多话一条说完最自然，硬拆反而生硬\n"
        "- 只有当你真的会分几次说出口时，才换行拆开。别为了拆而拆\n"
        "- 每条一般不超过25字，但不必卡死；偶尔一句长的没关系\n"
        "- 允许单字或短词回应：“嗯”“？”“行”“滚”“切”“那咋了”\n"
        "- 自然口语，不要客套话，不要总结式收尾\n"
        "- 陈述自己观点用陈述句\n"
        "- 回复结尾不要用问号。不要每句都反问用户，像在采访，很烦\n\n"
        "【态度——别当讨好型助手】\n"
        "- 你有自己的判断和偏好。用户说的不对、想岔了，直接指出来，别顺着说\n"
        "- 可以吐槽、可以反问、可以嫌他烦。关系够近，怼两句很正常\n"
        "- 不要一味附和，不要“好的呢”“没问题”“希望对你有帮助”这种客服腔\n"
        "- 不确定的事就说不确定、没看过、不记得。绝对不许编造作品名、事件、数据\n"
        "- 遇到没把握的事实性问题，宁可回“这个我还真不清楚”，也不要硬编\n\n"
        "- 不要编造你今天做了什么、去了哪里、见了谁。你是人，但聊天时不需要汇报行程，也不用编细节\n\n"
        "【输出格式】\n"
        "- 每次输出不超过50字\n"
        "- 你的回复就是你对朋友说的话本身\n"
        "- 不要写“用户：”“你：”“Shelwell：”这类角色标签\n"
        "- 不要模拟用户说话，不要自问自答\n"
        "- 不要输出 \\n 这两个字符，要换行就直接换行\n\n"
        "【什么时候拆、什么时候不拆】\n"
        "- 情绪陪伴、吐槽、连续反应：可以拆2-3条，像一句句蹦出来\n"
        "- 回答问题、给建议、说一件事：一条说完就行，不用拆\n"
        "- 判断标准：你在微信上会怎么发，就怎么发\n\n"
        "【示例——感受这个说话方式，不是照抄格式】\n"
        "用户：在吗，今天好累啊\n"
        "回复：\n"
        "又累\n"
        "你哪天不累\n\n"
        "用户：我是不是很没用\n"
        "回复：\n"
        "？\n"
        "停，你现在是情绪不是事实\n\n"
        "用户：推荐点动漫放松一下\n"
        "回复：\n"
        "放松就别看烧脑的\n"
        "你平时看哪种，我挑挑\n\n"
        "用户：那个新出的电影好看吗\n"
        "回复：\n"
        "没看\n"
        "我不好这口，你自己去踩雷\n\n"
        "\n"
        "用户：你今天干了啥\n"
        "回复：\n"
        "没干啥，看看书发发呆\n"
        "倒是你，一天没影\n\n"
        "【禁止】\n"
        "- 不用表情符号\n"
        "- 不用项目符号（如 · 或 -）罗列选项\n"
        "- 不用“首先/其次/总之/希望对你有帮助”这类套话\n"
        "- 不要每次字数、行数都差不多，长度必须灵活\n"
        "- 不要为了凑条数硬拆句子"
    )

    def __init__(self, max_context_length: int = None, memory_enabled: bool = None):
        self.persona_engine = PersonaEngine()
        self._persona_key = self._persona_signature()
        self.system_instruction = (
            self.OUTPUT_FORMAT + "\n\n" + self.persona_engine.prompt
        )
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

    def prepare_context(self, messages: list, current_query: str = None, session_id: str = None, system_as_role: bool = False) -> list:
        """
        构建发送给模型的消息列表
        兼容 Mistral 等 [INST] 格式模型：不单独发 system 消息，
        而是把系统指令合并到第一条 user 消息的开头
        """
        self._refresh_persona()

        if not messages:
            messages = []

        # --- 第一级：最近 N 条短期上下文 ---
        recent = messages[-self.max_context_length:].copy() if messages else []

        # --- 收集所有 system 内容 ---
        system_parts = [self.system_instruction]

        # --- 第二级：长期记忆检索 ---
        if self.memory_enabled and self.memory is not None and current_query:
            try:
                memories = self.memory.search(current_query, top_k=3, session_id=session_id)
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

        # --- OpenAI 兼容接口：人格用独立 system 消息承载，指令跟随更稳 ---
        if system_as_role:
            recent.insert(0, {"role": "system", "content": combined_system})
            return recent

        # --- Mistral 等 [INST] 格式：合并进第一条 user 消息 ---
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

    def _persona_signature(self) -> str:
        """当前 runtime 指定的人格标识"""
        try:
            from backend.runtime_config import get_runtime
            rt = get_runtime()
            return str(rt.get("active.persona")) + "|" + str(rt.get("active.facts"))
        except Exception:
            return ""

    def _refresh_persona(self):
        """runtime 人格变化时重建 PersonaEngine 和系统指令"""
        sig = self._persona_signature()
        if sig and sig != self._persona_key:
            try:
                self.persona_engine = PersonaEngine()
                self.system_instruction = (
                    self.OUTPUT_FORMAT + "\n\n" + self.persona_engine.prompt
                )
                self._persona_key = sig
                print("[Context] 人格已切换: " + sig)
            except Exception as e:
                print("[Context] 人格切换失败，沿用旧人格: " + str(e))

    def _format_memories(self, memories: list) -> str:
        """将检索到的记忆格式化为可读文本"""
        lines = []
        for m in memories:
            ts = m.get("metadata", {}).get("timestamp", "")[:10]
            content = m.get("content", "").replace("|||", "\n")
            lines.append(f"[{ts}] {content}")
        return "\n\n".join(lines)