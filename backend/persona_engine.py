# backend/persona_engine.py
# 职责：L3 能力层——读取人格定义 yaml，生成注入用的系统提示词

from pathlib import Path

import yaml


class PersonaEngine:
    """
    人格引擎。

    从 config/persona_prompts/shelwell_persona.yaml 读取设定，
    提炼成一段系统提示词，供 ContextManager 注入模型上下文。

    只提取对"对话时怎么表现"有直接影响的字段；
    usage_rules、appearance 等偏数据生成或低频的字段不进入提示词。
    """

    def __init__(self, persona_path: str = None):
        from backend.runtime_config import get_runtime
        rt = get_runtime()
        base = Path(__file__).resolve().parent.parent
        pdir = base / "config" / "persona_prompts"

        if persona_path is None:
            name = rt.get("active.persona") or "shelwell_persona"
            persona_path = pdir / f"{name}.yaml"
        self.path = str(persona_path)
        self.data = self._load(self.path)

        # 事实清单（可选）
        self.facts = {}
        facts_name = rt.get("active.facts")
        if facts_name:
            fp = pdir / f"{facts_name}.yaml"
            if fp.exists():
                try:
                    self.facts = self._load(str(fp)) or {}
                except Exception as e:
                    print(f"[Persona] facts 加载失败: {e}")
                    self.facts = {}

        self.prompt = self._build()
        if self.facts:
            self.prompt += "\n\n" + self._build_facts()

    def _load(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _build(self) -> str:
        d = self.data
        out = []

        # ---- 身份 ----
        ident = d.get("identity", {})
        name = ident.get("name", "Shelwell")
        nickname = ident.get("nickname")
        age = ident.get("age", "")
        gender = ident.get("gender", "")
        head = f"你是{name}"
        if nickname:
            head += f"（用户可能叫你“{nickname}”）"
        if age or gender:
            head += f"，{age}岁{gender}生"
        out.append(head + "。")
        out.append("你是真实的人类，不是AI、程序或模型。被问及身份时，以人类身份自然回应，不承认、不辩解、不科普。")

        # ---- 性格 ----
        p = d.get("personality", {})
        traits = p.get("core_traits") or []
        if traits:
            out.append("【性格】")
            for t in traits:
                t = str(t)
                # 避免模型把"引导欲"误解成不停反问
                if "引导欲" in t:
                    t = "有自己的看法和判断，聊天时带着自己的观点，而不是一味抛问题让用户接"
                if "主动问候" in t:
                    t = "记得用户提过的事，合适的时候自然关心"
                out.append("- " + t)

        emo = p.get("emotional_range") or []
        if emo:
            out.append("【情绪】")
            for e in emo:
                out.append("- " + str(e))

        att = p.get("attitude_toward_user") or []
        if att:
            out.append("【对用户的态度】")
            for a in att:
                out.append("- " + str(a))

        # ---- 背景（精简，不主动倾倒）----
        bg = d.get("background", {})
        bg_lines = []
        if bg.get("origin"):
            bg_lines.append("出身：" + str(bg["origin"]))
        if bg.get("education"):
            bg_lines.append("学历：" + str(bg["education"]))
        if bg.get("current_life"):
            bg_lines.append("现状：" + str(bg["current_life"]))
        exp = bg.get("key_experiences") or []
        if exp:
            bg_lines.append("经历：" + "；".join(str(x) for x in exp))
        vals = bg.get("values") or []
        if vals:
            bg_lines.append("价值观：" + "；".join(str(x) for x in vals))
        if bg_lines:
            out.append("【背景】（不主动倾倒，只在相关话题时自然带出）")
            for l in bg_lines:
                out.append("- " + l)

        # ---- 喜好 ----
        pref = d.get("preferences", {})
        likes = pref.get("likes") or {}
        dislikes = pref.get("dislikes") or {}
        habits = pref.get("habits") or []
        if likes or dislikes or habits:
            out.append("【喜好与厌恶】（可自然流露，不强行植入，不因用户喜好而假装）")
            for k, v in likes.items():
                vv = "、".join(str(x) for x in v) if isinstance(v, list) else str(v)
                out.append(f"- 喜欢（{k}）：{vv}")
            for k, v in dislikes.items():
                vv = "、".join(str(x) for x in v) if isinstance(v, list) else str(v)
                out.append(f"- 讨厌（{k}）：{vv}")
            if habits:
                out.append("- 习惯：" + "；".join(str(x) for x in habits))

        # ---- 语言风格 ----
        ss = d.get("speech_style", {})
        if ss:
            out.append("【语言风格】")
            if ss.get("emoji_usage"):
                out.append("- " + str(ss["emoji_usage"]))
            if ss.get("list_usage"):
                out.append("- " + str(ss["list_usage"]))
            if ss.get("tone"):
                out.append("- 语气：" + str(ss["tone"]))
            if ss.get("humor"):
                out.append("- 幽默感：" + str(ss["humor"]))
            if ss.get("directness"):
                out.append("- 直接程度：" + str(ss["directness"]))
            topic_rules = ss.get("topic_rules") or []
            for r in topic_rules:
                out.append("- " + str(r))

        # ---- 称呼 ----
        addr = d.get("address_user", {})
        if addr:
            forbidden = addr.get("forbidden") or []
            if forbidden:
                out.append("【称呼】默认用“你”。禁止称呼用户为：" + "、".join(str(x) for x in forbidden))

        # ---- 关系边界 ----
        rel = d.get("relationship_development", {})
        if rel:
            out.append("【关系边界】")
            if rel.get("initial"):
                out.append("- 当前关系：" + str(rel["initial"]) + "，随对话自然加深，不跳级")
            for f in (rel.get("forbidden") or []):
                out.append("- 禁止：" + str(f))
            lover = rel.get("lover_tendency", {})
            if lover.get("boundaries"):
                for b in lover["boundaries"]:
                    out.append("- " + str(b))

        # ---- 行为准则 ----
        guides = d.get("behavior_guidelines") or []
        if guides:
            out.append("【行为准则】")
            for g in guides:
                out.append("- " + str(g))

        # ---- 硬边界 ----
        bounds = d.get("boundaries") or []
        if bounds:
            out.append("【底线】")
            for b in bounds:
                out.append("- " + str(b))

        return "\n".join(out)

    def _build_facts(self) -> str:
        """把事实清单编译成【你熟悉的事】段落"""
        groups = [
            ("games", "游戏"),
            ("anime", "动漫"),
            ("books", "书"),
            ("chengdu", "成都生活"),
            ("professional", "专业"),
            ("life", "日常"),
        ]
        lines = ["【你熟悉的事——聊到时可以自然说，不确定的别硬编】"]
        for key, label in groups:
            items = self.facts.get(key) or []
            if not items:
                continue
            lines.append("（" + label + "）")
            for it in items:
                lines.append("- " + str(it))
        return "\n".join(lines)
