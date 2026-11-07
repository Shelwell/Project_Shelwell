# backend/session_summary.py
# 职责：把会话早期的对话压缩成摘要，供滚动窗口使用。
# 只在 token 探针触发时调用；摘要用当前模型，保证质量，不在每轮调用。

from __future__ import annotations

from backend.config_loader import get
from backend.llm import provider
from backend.people import PeopleRegistry


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


DIARY_PROMPT = """你是西尔维娅。请根据下面的对话，写一篇西尔维娅的第一人称日记。

要求：
1. 用“我”指西尔维娅自己。
2. 人物只用 [YY] 标识，例如 [佩奇]；不要写群昵称、私聊昵称、QQ 号。
3. 保留：发生了什么、谁说了什么、关系变化、未解决的事、重要情绪。
4. 不写寒暄、不总结成列表、不编造、不复述整段原文。
5. 只输出日记正文，不要标题、不要解释。
"""

DIARY_SUMMARY_PROMPT = """你是西尔维娅的长期记忆整理器。把下面几篇第一人称日记压缩成一段长期摘要。

要求：
1. 保留：人物关系、长期主线、重要承诺、未解决的事、反复出现的情绪。
2. 人物只用 [YY] 标识；不要写群昵称、私聊昵称、QQ 号。
3. 不要按天流水账；不要重复日记细节。
4. 只输出摘要正文，不要标题、不要列表、不要解释。
"""


def _clean_model_text(raw: str, people=None, max_chars: int = 0) -> str:
    text = (raw or "").strip()
    text = text.replace("[SILENT]", "").strip()
    if people is not None:
        text = people.scrub(text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def make_diary(messages: list, people=None, speaker_label: str = "",
               is_group: bool = False, model: str = "") -> str:
    """从一段对话生成第一人称日记；失败返回空。"""
    people = people or PeopleRegistry()
    lines = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = str(m.get("content", "") or "").strip()
        if not content or role == "system":
            continue
        content = people.scrub(content)
        if role == "assistant":
            lines.append("我：" + content[:1200])
        else:
            if is_group:
                lines.append(content[:1200])
            else:
                label = speaker_label or "[当前对方]"
                lines.append("%s：%s" % (label, content[:1200]))
    if not lines:
        return ""
    text = "\n".join(lines)
    if len(text) > 12000:
        text = text[:6000] + "\n……（中间省略）……\n" + text[-6000:]
    try:
        raw = provider.chat(
            [{"role": "system", "content": DIARY_PROMPT},
             {"role": "user", "content": text}],
            model_override=(model or None),
        )
    except Exception as e:
        print(f"[Diary] 日记生成失败: {e}")
        return ""
    return _clean_model_text(raw, people=people)


def summarize_diaries(diaries: list, previous_summary: str = "",
                      max_chars: int = 800, model: str = "",
                      people=None) -> str:
    """从日记生成长期摘要；失败返回空。"""
    people = people or PeopleRegistry()
    parts = []
    if previous_summary:
        parts.append("【更早的长期摘要】\n" + people.scrub(previous_summary))
    for r in diaries or []:
        if isinstance(r, dict):
            c = str(r.get("content") or "").strip()
            d = str(r.get("date") or "").strip()
        else:
            c, d = str(r or "").strip(), ""
        if c:
            parts.append("【日记 %s】\n%s" % (d, people.scrub(c)))
    if not parts:
        return ""
    text = "\n".join(parts)
    if len(text) > 12000:
        text = text[:6000] + "\n……（中间省略）……\n" + text[-6000:]
    try:
        raw = provider.chat(
            [{"role": "system", "content": DIARY_SUMMARY_PROMPT},
             {"role": "user", "content": text}],
            model_override=(model or None),
        )
    except Exception as e:
        print(f"[Diary] 长期摘要生成失败: {e}")
        return ""
    return _clean_model_text(raw, people=people, max_chars=max_chars)


def configured_model() -> str:
    return str(get("context.summary_model", "") or "").strip()
