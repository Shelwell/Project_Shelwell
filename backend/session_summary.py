# backend/session_summary.py
# 职责：把会话早期的对话压缩成摘要，供滚动窗口使用。
# 只在 token 探针触发时调用；摘要用当前模型，保证质量，不在每轮调用。

from __future__ import annotations

from backend.config_loader import get
from backend.llm import provider


SYSTEM_PROMPT = """你是对话记忆压缩器。把下面这段早期对话压缩成一段摘要，供角色继续对话时参考。

要求：
1. 保留：说话人、称呼、关系变化、约定、未解决的问题、当前话题、情绪变化。
2. 不要编造新事实；不要评价；不要写“用户/助手”的元信息，直接写发生了什么。
3. 不保留寒暄和重复内容。
4. 只输出摘要正文，不要标题、不要列表、不要解释。
5. 控制在 500–800 字。
"""


def summarize_history(messages: list, previous_summary: str = "",
                      max_chars: int = 800, model: str = "") -> str:
    """返回摘要；失败返回空字符串，由调用方回退到普通截断。"""
    lines = []
    if previous_summary:
        lines.append("【更早的摘要】\n" + previous_summary.strip())
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = str(m.get("content", "") or "").strip()
        if not content:
            continue
        label = "对方" if role == "user" else "她"
        lines.append(f"{label}：{content[:1200]}")
    if not lines:
        return ""

    user_text = "\n".join(lines)
    # 防止早期对话本身过长；保留开头和结尾，中间标记省略
    if len(user_text) > 12000:
        user_text = user_text[:6000] + "\n……（中间省略）……\n" + user_text[-6000:]
    try:
        raw = provider.chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user_text}],
            model_override=(model or None),
        )
    except Exception as e:
        print(f"[Summary] 会话摘要失败: {e}")
        return ""

    text = (raw or "").strip()
    text = text.replace("[SILENT]", "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def configured_model() -> str:
    return str(get("context.summary_model", "") or "").strip()
