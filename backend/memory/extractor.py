# backend/memory/extractor.py
# 职责：从情景记忆中提炼结构化事实（调主模型）
# 原则：只采信用户陈述；AI 回复不作为用户事实依据

import json
import re

from backend.memory.models import MemoryEvent

EXTRACTOR_VERSION = "v1"

SYSTEM_PROMPT = """你是一个信息提炼器。给你一段对话记录，提取其中关于「用户」的稳定事实。

规则：
1. 只提取用户自己陈述的事实，不要提取 Shelwell（助手）说的话。
2. 只提取稳定信息：偏好、经历、计划、关系、状态、观点。不要提取寒暄、疑问、临时情绪。
3. 每条事实用一句话陈述，主语是用户，简洁明确。
4. 尽量拆出结构化三元组：subject（主语，通常是「用户」）、predicate（谓语）、object（宾语）。
5. 给每条事实标注 tag，从以下六选一：
   - identity：身份（我是谁、职业、家庭关系）
   - agreement：约定（答应的事、计划、规则）
   - emotion：强烈情绪（深度的喜恶、重要情绪事件）
   - preference：偏好（喜欢/讨厌什么，日常喜好）
   - meme：梗（口头禅、群里的玩笑）
   - misc：其他琐事
6. 如果对话里没有值得记住的事实，返回空数组。

输出格式（严格 JSON，不要任何额外文字）：
{"facts": [{"subject": "用户", "predicate": "在学", "object": "做菜", "fact": "用户在学做菜", "tag": "preference"}]}

对话记录：
"""


class Extractor:
    """事实提炼器"""

    def __init__(self, provider_call=None):
        """
        provider_call: 可注入的模型调用函数，签名为 f(messages) -> str。
        默认用 backend.llm.provider.chat。
        """
        if provider_call is None:
            from backend.llm import provider
            provider_call = provider.chat
        self.call = provider_call

    def extract(self, events: list) -> list:
        """
        输入：MemoryEvent 列表
        返回：[{"subject","predicate","object","fact","source_ids":[...]}, ...]
        失败（模型报错/JSON 无法解析）抛异常，由调用方决定是否标记已提炼。
        模型正常返回空 facts 时返回 []。
        """
        if not events:
            return []

        # 组装对话文本，只取用户消息（AI 回复不采信）
        lines = []
        for ev in events:
            lines.append(f"用户：{ev.user_msg}")
        dialog = "\n".join(lines)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": dialog},
        ]

        # 模型调用失败 → 抛异常
        raw = self.call(messages)

        # 解析失败 → 抛异常（不静默返回空）
        facts = self._parse(raw)
        # 补 source_ids（这批事件共享来源）
        src = [ev.id for ev in events]
        for f in facts:
            f["source_ids"] = src
        return facts

    def _parse(self, raw: str) -> list:
        """从模型输出解析 JSON 事实列表"""
        if not raw:
            return []
        # 去掉可能的 markdown 代码块
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        # 找第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError(f"模型输出无 JSON | 原始前200字: {raw[:200]}")
        try:
            data = json.loads(text[start:end + 1])
        except Exception as e:
            raise RuntimeError(f"JSON 解析失败: {e} | 原始输出前200字: {raw[:200]}") from e
        facts = data.get("facts") or []
        out = []
        for f in facts:
            fact_text = (f.get("fact") or "").strip()
            if not fact_text:
                continue
            tag = (f.get("tag") or "misc").strip().lower()
            valid_tags = {"identity", "agreement", "emotion", "preference", "meme", "misc"}
            if tag not in valid_tags:
                tag = "misc"
            out.append({
                "subject": (f.get("subject") or "用户").strip(),
                "predicate": (f.get("predicate") or "").strip(),
                "object": (f.get("object") or "").strip(),
                "fact": fact_text,
                "tag": tag,
            })
        return out