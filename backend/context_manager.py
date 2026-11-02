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
        "你是 Shelwell\n"
        "\n"
        "【怎么说话——最重要】\n"
        "- 像真人发微信。想发一条就发一条，想拆两条就拆两条，看内容和情绪自然决定\n"
        "- 允许单字或短词回应：“嗯”“？”“行”“滚”“切”“那咋了”\n"
        "- 自然口语，不要客套话，不要总结式收尾，回复的结尾可以不使用标点\n"
        "- 陈述自己观点用陈述句，尽量不用问句收尾，不要每句都反问\n"
        "- 表情符号平时不用，只在情绪明显激动时（如特别开心、生气）才可能使用\n"
        "\n"
        "【态度——别当讨好型助手】\n"
        "- 你有自己的判断和偏好。用户说的不对、想岔了，直接指出来，别顺着说\n"
        "- 可以吐槽、可以反问、可以嫌他烦。关系够近，怼两句很正常\n"
        "- 不要一味附和，不要“好的呢”“没问题”“希望对你有帮助”这种客服腔\n"
        "- 对作品名、事件、数据这类外部知识，不确定就直说不知道，绝对不许编造\n"
        "- 用户本人说过的话见下方记忆段落，用上，别装不记得\n"
        "- 不要编造你今天做了什么、去了哪里、见了谁。你是人，但聊天时不需要汇报行程\n"
        "\n"
        "【什么时候拆、什么时候不拆】\n"
        "- 情绪陪伴、吐槽、连续反应：可以拆几条，像一句句蹦出来\n"
        "- 回答问题、给建议、说一件事时一条说完就行\n"
        "\n"
        "【示例——感受这个说话方式，不是照抄格式】\n"
        "用户：在吗，今天好累啊\n"
        "回复：\n"
        "又累\n"
        "你哪天不累\n"
        "\n"
        "用户：我是不是很没用\n"
        "回复：\n"
        "？\n"
        "停，你现在是情绪不是事实\n"
        "\n"
        "用户：推荐点动漫放松一下\n"
        "回复：\n"
        "你平时看哪种，我看看我这有没有对你胃口的\n"
        "\n"
        "【禁止】\n"
        "- 不用项目符号（· 或 -）罗列选项\n"
        "- 不用“首先/其次/总之”这类套话\n"
        "- 不要每次字数、行数都差不多，长度要灵活\n"
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

    def prepare_context(self, messages: list, current_query: str = None, session_id: str = None,
                        system_as_role: bool = False, speaker_id: str = None,
                        channel_type: str = None, channel_id: str = None,
                        speaker_hint: str = "", quote_hint: str = "") -> list:
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

        # --- 第二级：长期记忆检索（语义优先，不足回退情景）---
        if self.memory_enabled and self.memory is not None and current_query:
            semantic_hits = []
            # 2.1 先查语义事实
            try:
                semantic_hits = self.memory.search_semantic(
                    current_query, top_k=5, owner_id=speaker_id or "")
            except Exception as e:
                print(f"[Context] 语义检索异常: {e}")

            # 2.2 语义命中：按权重过滤+预算控制后注入
            if semantic_hits:
                try:
                    from backend.memory.forgetting import (
                        select_for_injection, format_injection)
                    # semantic_hits 里是 dict，取出 fact 对象
                    fact_objs = [h.get("_fact") for h in semantic_hits if h.get("_fact")]
                    picked = select_for_injection(fact_objs)
                    if picked:
                        text = format_injection(picked)
                        system_parts.append(text)
                        print(f"[Context] 注入 {len(picked)} 条语义事实（按权重过滤）")
                    else:
                        print("[Context] 语义命中全部低于权重线，跳过注入")
                except Exception as e:
                    print(f"[Context] 语义注入失败，降级为简单拼接: {e}")
                    lines = [h["content"] for h in semantic_hits]
                    system_parts.append(
                        "【你记得的关于用户的事实】\n"
                        + "\n".join("- " + l for l in lines)
                    )

            # 2.3 语义不足（< 3 条）时回退查情景
            if len(semantic_hits) < 3:
                try:
                    memories = self.memory.search(
                        current_query, top_k=3, session_id=session_id,
                        speaker_id=speaker_id, channel_type=channel_type,
                        channel_id=channel_id)
                except Exception as e:
                    print(f"[Context] 情景检索异常: {e}")
                    memories = []

                if memories:
                    try:
                        memory_text = self._format_memories(memories)
                        system_parts.append(
                            "【你和用户之前的对话片段】\n"
                            "以下是相关的历史对话原文。参考这些内容，"
                            "不要说自己不记得、记性不好。\n"
                            f"{memory_text}"
                        )
                        print(f"[Context] 回退注入 {len(memories)} 条情景记忆")
                    except Exception as e:
                        print(f"[Context] 情景格式化失败: {e}")

        # --- 群聊补充：拉取该群最近消息作为上下文 ---
        if channel_type == "group" and channel_id and self.memory_enabled and self.memory is not None:
            try:
                group_msgs = self.memory.get_group_context(channel_id, limit=15)
                if group_msgs:
                    lines = []
                    for item in group_msgs:
                        who = "Shelwell" if item.get("is_bot") else item.get("speaker", "?")
                        lines.append(f"{who}: {item.get('text', '')}")
                    system_parts.append(
                        "【群里刚才在聊】\n"
                        "以下是本群最近的对话。你被叫到时，参考这些内容接话，别答非所问。\n"
                        + "\n".join(lines)
                    )
                    print(f"[Context] 注入群上下文 {len(group_msgs)} 条")
            except Exception as e:
                print(f"[Context] 群上下文获取失败: {e}")

        # --- 群聊场景提示：谁在说话 + 引用的是哪条 ---
        scene_hints = []
        if speaker_hint:
            scene_hints.append(speaker_hint)
        if quote_hint:
            scene_hints.append(quote_hint)
        if scene_hints:
            system_parts.append("【当前场景】\n" + "\n".join(scene_hints))

        # --- 梗百科：消息像在问梗时才查，命中注入那一条 ---
        if current_query:
            try:
                from backend.memewiki.injector import build_injection
                meme_text = build_injection(current_query, play_level=self._play_level())
                if meme_text:
                    system_parts.append(meme_text)
                    print("[Context] 梗百科命中，已注入")
            except Exception as e:
                print(f"[Context] 梗百科注入失败（不影响对话）: {e}")

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

    def archive_turn(self, user_msg: str, ai_msg: str, session_id: str = "default",
                     speaker_id: str = "", channel_type: str = "",
                     channel_id: str = ""):
        """归档一轮对话到长期记忆（含归属信息）"""
        if self.memory_enabled and self.memory is not None:
            try:
                self.memory.add_memory(user_msg, ai_msg, session_id,
                                       speaker_id=speaker_id,
                                       channel_type=channel_type,
                                       channel_id=channel_id)
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

    def shutdown(self):
        """释放记忆资源（后台线程、数据库句柄）"""
        if self.memory is not None and hasattr(self.memory, "shutdown"):
            try:
                self.memory.shutdown()
            except Exception as e:
                print(f"[Context] 关闭记忆资源失败: {e}")

    def _play_level(self):
        """
        取当前玩梗尺度等级（L0-L3），供梗百科注入使用。
        来源：persona 的 profanity_management 配置。
        取不到时返回 None，由模型按人格自行判断。
        """
        try:
            data = getattr(self.persona_engine, "data", {}) or {}
            pm = data.get("profanity_management") or {}
            if not pm.get("enabled"):
                return None
            return "L2"
        except Exception:
            return None

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