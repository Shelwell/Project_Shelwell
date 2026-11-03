# backend/orchestrator.py
# 职责：L4 编排层——一次对话的完整流程
# 收消息 → 建上下文 → 调模型 → 清洗回复 → 归档 → 返回

from backend.config_loader import get
from backend.context_manager import ContextManager
from backend.llm import provider
from backend.reply_format import split_bubbles


# 模型偶尔会把系统提示词当回复输出，这类内容不能进记忆库
_PROMPT_POLLUTION_MARKS = [
    "你是 Shelwell", "【怎么说话", "【你记得的", "【输出格式",
    "【禁止】", "【态度", "【说话风格", "【示例——",
]

# 模型输出的占位符/元提示，不是真回复
_PLACEHOLDER_MARKS = [
    "(等待用户输入)", "（等待用户输入）", "(待补充)", "（待补充）",
    "(此处省略)", "（此处省略）", "(省略)", "（省略）",
]


def _looks_like_prompt(text: str) -> bool:
    """判断回复是否像系统提示词或占位符（不应进记忆）"""
    if not text:
        return False
    if any(m in text for m in _PLACEHOLDER_MARKS):
        return True
    hits = sum(1 for m in _PROMPT_POLLUTION_MARKS if m in text)
    return hits >= 1


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

    def chat(self, messages: list, session_id: str = "default", current_query: str = None,
             speaker_id: str = None, channel_type: str = None, channel_id: str = None,
             speaker_hint: str = "", quote_hint: str = "",
             keep_alive: str = None) -> dict:
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
            # 人格指令一律用独立 system 消息承载：Ollama 的 chat template
            # （Mistral Nemo / Qwen 系）都支持 system role；塞进 user 消息会让
            # 指令优先级降到最低，且聊到 30 轮后完全衰减。
            prepared = self.context.prepare_context(
                messages, current_query=current_query, session_id=session_id,
                system_as_role=True,
                speaker_id=speaker_id, channel_type=channel_type,
                channel_id=channel_id,
                speaker_hint=speaker_hint, quote_hint=quote_hint,
            )
        except Exception as e:
            print(f"[Orchestrator] 上下文构建失败，降级为纯上下文: {e}")
            prepared = messages[-get("context.max_length", 30):]

        # 2. 调模型
        try:
            raw_reply = self._call_model(prepared, keep_alive=keep_alive)
        except Exception as e:
            return {"reply": "", "ok": False, "error": f"model_error: {e}"}

        # 3. 清洗 + 切气泡（格式交给代码，模型只管内容）
        reply = self._clean_reply(raw_reply)
        bubbles = split_bubbles(
            reply,
            limit=get("reply.max_chars_per_bubble", 40),
            max_bubbles=get("reply.max_bubbles", 3),
        )
        if not bubbles:
            return {"reply": "", "ok": False, "error": "empty_reply"}
        reply = "\n".join(bubbles)

        # 4. 归档（跳过 prompt 污染 / 纯问梗）
        if current_query and _looks_like_prompt(reply):
            print("[Orchestrator] 回复疑似系统提示词，跳过归档")
        elif current_query and self._is_pure_meme_ask(current_query):
            print("[Orchestrator] 纯问梗，跳过归档（不污染记忆）")
        elif current_query:
            try:
                self.context.archive_turn(
                    user_msg=current_query, ai_msg=reply, session_id=session_id,
                    speaker_id=speaker_id or "",
                    channel_type=channel_type or "",
                    channel_id=channel_id or "",
                )
            except Exception as e:
                print(f"[Orchestrator] 归档失败（不影响对话）: {e}")

        return {"reply": reply, "bubbles": bubbles, "ok": True, "error": None}

    def shutdown(self):
        """释放编排层持有的资源"""
        if hasattr(self.context, "shutdown"):
            try:
                self.context.shutdown()
            except Exception as e:
                print(f"[Orchestrator] 关闭失败: {e}")

    @staticmethod
    def _is_pure_meme_ask(text: str) -> bool:
        """
        判断是否纯问梗（「XX是什么意思」），这类对话不归档。
        聊天中提及梗（mention）仍正常归档，作为对话经历。
        """
        try:
            from backend.memewiki.matcher import classify
            _, mode = classify(text)
            return mode == "ask"
        except Exception:
            return False

    def _call_model(self, prepared_messages: list, keep_alive: str = None) -> str:
        """调用 L2 provider，返回原始文本"""
        return provider.chat(prepared_messages, keep_alive=keep_alive)

    def _clean_reply(self, text: str) -> str:
        """
        清洗模型输出。脚本能做的都在这做，提示词只管风格：
        1. 字面 \\n 转真换行
        2. 去角色标签（用户：/你：/Shelwell：）
        3. 去方括号昵称前缀（[张三] / 【张三】）
        4. 去引用/回复前缀（引用张三：/回复某人：）
        表情符号不删，由提示词引导少用。
        """
        if not text:
            return ""
        import re
        text = text.replace("\\n", "\n")

        # 角色标签
        tags = ["用户：", "用户:", "Shelwell：", "Shelwell:", "你：", "你:"]

        # 昵称前缀：[张三] 或 【张三】
        nick_re = re.compile(r"^\s*[\[【][^\]】]{1,20}[\]】]\s*")
        # 引用/回复前缀：引用张三：/回复某人：/引用：/回复：
        ref_re = re.compile(r"^\s*(?:引用|回复)[^：:]{0,20}[：:]\s*")

        cleaned = []
        for line in text.split("\n"):
            # 去角色标签
            for tag in tags:
                if line.startswith(tag):
                    line = line[len(tag):]
                    break
            # 去昵称前缀（可多次，防嵌套）
            for _ in range(3):
                new_line = nick_re.sub("", line, count=1)
                if new_line == line:
                    break
                line = new_line
            # 去引用/回复前缀
            line = ref_re.sub("", line, count=1)
            cleaned.append(line)

        result = "\n".join(cleaned).strip()
        return result

    def get_stats(self, messages: list) -> dict:
        """透传 ContextManager 的统计信息"""
        try:
            return self.context.get_stats(messages)
        except Exception:
            return {}