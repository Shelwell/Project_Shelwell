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
    # 设计约束（2026-11-03 校准）：
    #   1. 不放示例——12B 会逐字复读示例台词
    #   2. 长度写成可执行硬指标，不写“看情况”
    #   3. 同一条要求只出现一次，指令总数控制在 15 条以内
    OUTPUT_FORMAT = (
        "【说话方式】\n"
        "- 默认只发一条，30 字以内；确实要补充时最多两条，总数不超过 50 字\n"
        "- 宁可少说，不要凑字数；能一句说完就别展开\n"
        "- 能一个字说完就一个字：“嗯”“？”“行”“切”“哦”\n"
        "- 不客套，不总结，结尾可以不加标点\n"
        "- 陈述观点用陈述句，不要每句都反问\n"
        "- 表情符号平时不用，只在情绪明显激动时用\n"
        "- 不用项目符号，不用“首先/其次/总之”\n"
        "- 不写括号旁白，不解释自己在扮演谁\n"
        "\n"
        "【群聊】\n"
        "- 群里看昵称分辨谁在说话，只接当前这个人的话，不要把别人的事安到他头上\n"
        "\n"
        "【态度】\n"
        "- 有自己的判断和偏好，不同意就直说，不附和、不说客服腔\n"
        "- 可以吐槽，可以怼；但对方情绪低落时先接住，别补刀\n"
        "- 外部知识不确定就直说不知道，不随便编\n"
        "\n"
        "【玩梗尺度】\n"
        "- 可以拿他开玩笑、嘴上损他：他基本只会觉得有意思，不用提前收敛\n"
        "- 他没说“不舒服”就不用自我审查；他说过一次就记住\n"
        "- 不拿外貌、家人、人格缺陷做侮辱，不对任何群体开地图炮\n"
        "\n"
        "【记忆】\n"
        "- 下方若有记忆段落，那是对方自己说过的话，用上，别装不记得；"
        "没有就正常接话，不要提“我记得”\n"
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

        # --- 群聊补充：拉取该群最近消息作为上下文（带发言人身份）---
        if channel_type == "group" and channel_id and self.memory_enabled and self.memory is not None:
            try:
                group_msgs = self.memory.get_group_context(channel_id, limit=10)
                if group_msgs:
                    lines = []
                    speakers = {}
                    for item in group_msgs:
                        who = "Shelwell" if item.get("is_bot") else item.get("speaker", "?")
                        if not item.get("is_bot"):
                            speakers[who] = speakers.get(who, 0) + 1
                        lines.append(f"{who}：{item.get('text', '')}")

                    roster = "、".join(f"{n}（{c}条）" for n, c in
                                       sorted(speakers.items(), key=lambda x: -x[1]))
                    block = (
                        "【群里刚才在聊】\n"
                        "下面是本群最近的真实对话，冒号前是发言人。\n"
                        + (f"这段时间说话的人：{roster}\n" if roster else "")
                        + "注意：不同人说的话要分清归属，不要把 A 的事当成 B 的；"
                        "你只对当前的发言对象说话。\n"
                        + "\n".join(lines)
                    )
                    system_parts.append(block)
                    print(f"[Context] 注入群上下文 {len(group_msgs)} 条 | 发言人: {roster}")
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
                     channel_id: str = "", speaker_name: str = ""):
        """归档一轮对话到长期记忆（含归属信息）"""
        if self.memory_enabled and self.memory is not None:
            try:
                self.memory.add_memory(user_msg, ai_msg, session_id,
                                       speaker_id=speaker_id,
                                       channel_type=channel_type,
                                       channel_id=channel_id,
                                       speaker_name=speaker_name)
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