# backend/memory/extractor.py
# 职责：从情景记忆中提炼结构化事实（调主模型）
# 原则：只采信用户陈述；AI 回复不作为用户事实依据；
#       主语必须是发言人本人（昵称/QQ），禁止用“用户”吞掉身份

import json
import re

from backend.memory.models import MemoryEvent
from backend.people import PeopleRegistry

EXTRACTOR_VERSION = "v2"   # v2：按发言人提取，主语用昵称

# {who} = 发言人称呼，{owner} = QQ/ID
SYSTEM_PROMPT_TMPL = """你是一个信息提炼器。下面这些消息全部来自同一个人：{who}。
请只提取关于 {who} 本人的稳定事实。

规则：
1. 主语、宾语中的人物必须使用固定人物标识 [YY]，例如 [佩奇]、{self}。
2. 主语必须是 {who}；禁止使用「用户」「对方」「他」「她」这类模糊称呼。
3. {self} 是助手本人；客观事实中提到助手时写 {self}，不要写“我”“她”或裸名。
4. 只提取陈述句里稳定的信息：偏好、经历、计划、关系、状态、观点；跳过寒暄、疑问、临时情绪。
5. 不采信 {self}（助手）的回复；消息里提到的其他人（朋友、同事、家人）的事也不要记到 {who} 头上。
6. 每条 fact 用一句话说清，人物用 [YY] 表示，例如「[佩奇]觉得{self}的猫很可爱」。
7. 三元组的 subject 同样写「{who}」；object 里提到人物时也要用 [YY]。
8. tag 六选一：identity / agreement / emotion / preference / meme / misc。
9. 没有值得记住的事实就返回空数组。

输出格式（严格 JSON，不要任何额外文字）：
{{"facts": [{{"subject": "{who}", "predicate": "觉得", "object": "{self}的猫很可爱", "fact": "{who}觉得{self}的猫很可爱", "tag": "misc"}}]}}

对话记录：
"""

_VAGUE_SUBJECTS = {"用户", "对方", "他", "她", "我", "这个人", "该用户", "user", "ta", "TA"}


class Extractor:
    """事实提炼器"""

    def __init__(self, provider_call=None):
        """
        provider_call: 可注入的模型调用函数，签名为 f(messages) -> str。
        默认用 backend.llm.provider.chat。
        提炼可单独指定模型（memory.extractor_model）；留空则跟随当前对话模型。
        """
        if provider_call is None:
            from backend.llm import provider
            provider_call = provider.chat
        self.call = provider_call
        self.model = self._resolve_model()
        self.people = PeopleRegistry()

    @staticmethod
    def _resolve_model() -> str:
        """提炼模型：runtime 优先，其次部署配置；空串表示跟随主模型"""
        try:
            from backend.runtime_config import get_runtime
            m = (get_runtime().get("memory.extractor_model") or "").strip()
            if m:
                return m
        except Exception:
            pass
        try:
            from backend.config_loader import get
            return (get("memory.extractor_model", "") or "").strip()
        except Exception:
            return ""

    def _call_model(self, messages: list) -> str:
        """调用模型；带模型覆盖，且兼容不接受该参数的注入实现"""
        if not self.model:
            return self.call(messages)
        try:
            return self.call(messages, model_override=self.model)
        except TypeError:
            return self.call(messages)

    def build_messages(self, events: list, speaker_name: str = "",
                       owner_id: str = "") -> tuple:
        """组装提炼用 messages，返回 (messages, who)"""
        raw_who = (speaker_name or "").strip() or str(owner_id or "").strip()
        who = self.people.label(owner_id, fallback=raw_who)
        lines = []
        for ev in events:
            if not ev.user_msg:
                continue
            content = self.people.scrub(ev.user_msg)
            content = self.people.scrub_self(content)
            lines.append("%s：%s" % (who, content))
        self_ref = self.people.self_ref() or "[助手]"
        system = SYSTEM_PROMPT_TMPL.format(who=who, self=self_ref)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(lines)},
        ], who

    def extract(self, events: list, speaker_name: str = "",
                owner_id: str = "") -> list:
        """
        输入：MemoryEvent 列表（属于同一个人）
        返回：[{"subject","predicate","object","fact","tag","source_ids":[...]}, ...]
        失败（模型报错/JSON 无法解析）抛异常，由调用方决定是否标记已提炼。
        """
        if not events:
            return []

        messages, who = self.build_messages(events, speaker_name, owner_id)

        # 模型调用失败 → 抛异常
        raw = self._call_model(messages)

        # 解析失败 → 抛异常（不静默返回空）
        facts = self._parse(raw, who)
        # 补 source_ids（这批事件共享来源）
        src = [ev.id for ev in events]
        for f in facts:
            f["source_ids"] = src
        return facts

    def _parse(self, raw: str, who: str = "[未知]") -> list:
        """从模型输出解析 JSON 事实列表；主语归一化为发言人身份"""
        if not raw:
            return []
        who = self.people.scrub(str(who or "").strip())
        if who and not who.startswith("["):
            who = self.people.label_for_name(who) or ("[" + who + "]")
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

            # 主语兜底 + 固定标识归一化
            raw_subject = (f.get("subject") or "").strip()
            subject = self.people.scrub(raw_subject)
            subject = self.people.scrub_self(subject)
            if not subject or subject in _VAGUE_SUBJECTS:
                subject = who
            elif not subject.startswith("["):
                mapped = self.people.label_for_name(subject)
                subject = mapped or ("[" + subject + "]")
            # object/ fact 中的已知人物昵称统一成 [YY]；self 用 [西尔维娅]
            obj = self.people.scrub((f.get("object") or "").strip())
            obj = self.people.scrub_self(obj)
            fact_text = self.people.scrub(fact_text)
            fact_text = self.people.scrub_self(fact_text)
            if raw_subject and not raw_subject.startswith("[") and fact_text.startswith(raw_subject):
                fact_text = subject + fact_text[len(raw_subject):]
            for vague in _VAGUE_SUBJECTS:
                if fact_text.startswith(vague):
                    fact_text = who + fact_text[len(vague):]
                    break

            out.append({
                "subject": subject,
                "predicate": (f.get("predicate") or "").strip(),
                "object": obj,
                "fact": fact_text,
                "tag": tag,
            })
        return out
