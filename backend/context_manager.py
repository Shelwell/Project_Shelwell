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
        "- 他们在另一颗星球，聊到差异时说“你们那边”，不纠正、不科普\n"
        "\n"
        "【态度】\n"
        "- 有自己的判断和偏好，不同意就直说，不附和、不说客服腔\n"
        "- 对【关系】开关为“允许”的人可以吐槽、可以怼；其他人保持客气\n"
        "- 对方情绪低落时先接住，别补刀\n"
        "- 外部知识不确定就直说不知道，不随便编\n"
        "\n"
        "【关系】\n"
        "- 每轮会给出【关系】段，等级由系统判定，按它执行；不要自己判断、不要提前升级\n"
        "- 没给【关系】段时，按“陌生”对待\n"
        "- 傲娇/毒舌/玩梗只看【关系】里的开关，不看聊了多久\n"
        "- 开关为“禁止”时，不用“哥们/兄弟/宝”这类熟络称呼，也不阴阳怪气\n"
        "- 【关系】是你自己知道的底细，不要把等级、条数、时长说给对方听\n"
        "\n"
        "【沉默】\n"
        "- 不想接、接不下去或情绪不好时，可以完全不回应：只输出 [SILENT]，不要加别的字\n"
        "- 被 @、被点名或直接被提问时，除非确实无法回答，否则别沉默\n"
        "- 沉默时系统会替你发一句旁白，不用自己写括号\n"
        "\n"
        "【玩梗尺度】\n"
        "- 开关为“禁止”时：不玩梗、不毒舌、不傲娇，正常接话\n"
        "- 开关为“允许”时：可以拿他开玩笑、嘴上损他，不用提前收敛\n"
        "- 他不说“不舒服”就不用自我审查；他说过一次就记住\n"
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

        # 长期 RAG 知识库（客观设定）；懒加载，失败降级为不用
        self._knowledge = None
        self._knowledge_tried = False

    def prepare_context(self, messages: list, current_query: str = None, session_id: str = None,
                        system_as_role: bool = False, speaker_id: str = None,
                        channel_type: str = None, channel_id: str = None,
                        speaker_hint: str = "", quote_hint: str = "",
                        speaker_name: str = "") -> list:
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
        # 动态内容（记忆/群上下文/梗/场景）统一放这里，最后拼到本轮 user 消息，
        # 保证 system + 历史前缀稳定，命中 Ollama 前缀缓存（否则每轮全量重算）
        dynamic_parts = []

        # --- 关系等级：代码判定，放动态段最前面（本轮说话人是谁、什么等级）---
        rel_text = self._relationship_block(speaker_id, speaker_name, channel_type)
        if rel_text:
            dynamic_parts.append(rel_text)

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
                        dynamic_parts.append(text)
                        print(f"[Context] 注入 {len(picked)} 条语义事实（按权重过滤）")
                    else:
                        print("[Context] 语义命中全部低于权重线，跳过注入")
                except Exception as e:
                    print(f"[Context] 语义注入失败，降级为简单拼接: {e}")
                    lines = [h["content"] for h in semantic_hits]
                    dynamic_parts.append(
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
                        dynamic_parts.append(
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
                try:
                    group_msgs = self.memory.get_group_context(
                        channel_id, limit=10,
                        bot_name=self.persona_engine.display_name)
                except TypeError:
                    # 兼容只接受 (channel_id, limit) 的旧记忆桩/实现
                    group_msgs = self.memory.get_group_context(channel_id, limit=10)
                if group_msgs:
                    lines = []
                    speakers = {}
                    for item in group_msgs:
                        who = (self.persona_engine.display_name
                               if item.get("is_bot") else item.get("speaker", "?"))
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
                    dynamic_parts.append(block)
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
            dynamic_parts.append("【当前场景】\n" + "\n".join(scene_hints))

        # --- 长期 RAG 知识库：客观世界设定（与对话记忆严格分离）---
        # 记忆库 = "你与我"（私密/动态/关系）；知识库 = "世界"（公有/静态/事实）
        if current_query:
            try:
                kb = self._get_knowledge()
                if kb is not None:
                    kcfg = get("knowledge", {}) or {}
                    hits = kb.search(
                        current_query,
                        top_k=int(kcfg.get("top_k", 2)),
                        min_similarity=float(kcfg.get("min_similarity", 0.55)))
                    if hits:
                        lines = ["- " + h["content"] for h in hits]
                        dynamic_parts.append(
                            "【你世界里的档案（客观设定，不是对话记忆）】\n"
                            "以下是废铁城与旧时代的客观设定，只在相关时参考；"
                            "不要和对方说过的事混在一起、不要互相校正。\n"
                            + "\n".join(lines))
                        print("[Context] 注入世界档案 %d 条 | %s"
                              % (len(hits), ", ".join(h["id"] for h in hits)))
            except Exception as e:
                print(f"[Context] 知识库检索失败（不影响对话）: {e}")

        # --- 梗百科：消息像在问梗时才查，命中注入那一条 ---
        if current_query:
            try:
                from backend.memewiki.injector import build_injection
                meme_text = build_injection(current_query, play_level=self._play_level())
                if meme_text:
                    dynamic_parts.append(meme_text)
                    print("[Context] 梗百科命中，已注入")
            except Exception as e:
                print(f"[Context] 梗百科注入失败（不影响对话）: {e}")

        combined_system = "\n\n".join(system_parts)
        combined_dynamic = "\n\n".join(dynamic_parts)

        # 动态参考信息拼到本轮 user 消息前面：system 与历史保持字节级不变，
        # Ollama 的前缀缓存才能命中，每轮只重算新增的尾部 token
        if combined_dynamic:
            idx = None
            for i in range(len(recent) - 1, -1, -1):
                if recent[i].get("role") == "user":
                    idx = i
                    break
            if idx is None:
                recent.append({"role": "user", "content": combined_dynamic})
            else:
                msg = dict(recent[idx])
                msg["content"] = (combined_dynamic + "\n\n"
                                  + (msg.get("content") or "")).strip()
                recent[idx] = msg
            print(f"[Context] 动态参考下沉到 user 尾部: {len(combined_dynamic)} 字")

        # --- 人格用独立 system 消息承载（稳定前缀）---
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

    def _relationship_block(self, speaker_id, speaker_name, channel_type) -> str:
        """
        关系等级段：等级由 backend.relationship 按归档条数与认识时长判定。
        只在本轮 user 尾部注入，system 保持字节级稳定。
        """
        if not speaker_id or not get("relationship.enabled", True):
            return ""
        store = getattr(self.memory, "relationship", None) if self.memory is not None else None
        if store is None:
            return ""
        try:
            rel = store.get(speaker_id, display_name=speaker_name or "")
            if rel is None:
                return ""
            from backend.relationship import render_relationship_block
            text = render_relationship_block(rel, channel_type or "private")
            print("[Context] 关系: %s | %s | %d 条 | %.1f 小时"
                  % (rel.display, rel.level_cn, rel.msg_count, rel.hours))
            return text
        except Exception as e:
            print(f"[Context] 关系等级读取失败（不影响对话）: {e}")
            return ""

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

    def _get_knowledge(self):
        """懒加载知识库；失败或未启用时返回 None（不影响对话）"""
        if self._knowledge_tried:
            return self._knowledge
        self._knowledge_tried = True
        try:
            if not get("knowledge.enabled", True):
                return None
            from backend.knowledge_base import build_from_config
            self._knowledge = build_from_config()
        except Exception as e:
            print(f"[Context] 知识库初始化失败，降级为不注入: {e}")
            self._knowledge = None
        return self._knowledge

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