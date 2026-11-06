# backend/group_policy.py
# 职责：L3 能力层——群聊接话决策（影子模式先行的规则引擎）
# 设计：
#   1. 先判"是不是对我说"（@我 / 回复我 / @别人），再判"现在合不合适说"
#   2. 默认宁漏不多：没有明确信号就沉默
#   3. 纯本地计算，不调模型、不联网，单条决策 < 1ms
# 说明：语义层（bge-m3 相关性）留待影子数据校准后再接入，见 use_embeddings 开关

import re
import time
from collections import deque
from dataclasses import dataclass, field

DEFAULTS = {
    "threshold": 0.55,              # 参与意愿分达到多少才考虑接话
    "active_hours": ["19:00-24:00"],  # 群活跃时段（经验值：晚上 7 点到 12 点）
    "quiet_hours": ["00:30-09:00"],   # 静默时段：除非 @ 我，否则不说话
    "cooldown_seconds": 90,         # 两次主动接话的最小间隔
    "rapid_window_seconds": 30,     # 判断"热聊"的窗口
    "rapid_min_msgs": 3,            # 窗口内这么多条且多人发言 → 热聊
    "min_len": 4,                   # 低于这个长度的消息不主动接
    "question_bonus": 0.60,         # 开放式提问的加分（安静时段提问通常值得接）
    "interest_bonus": 0.20,         # 命中她熟悉领域（facts）的加分
    "topic_weight": 0.25,           # 话题连续性权重
    "rapid_penalty": 0.40,          # 热聊惩罚
    "quiet_penalty": 0.60,          # 静默时段惩罚
    "cooldown_penalty": 0.50,       # 冷却惩罚
    "name_aliases": ["西尔维娅", "西儿", "小西", "维娅"],
}


def _parse_windows(specs) -> list:
    """把 ["19:00-24:00", "08:00-09:30"] 解析成 [(start_min, end_min), ...]"""
    out = []
    for s in specs or []:
        m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", str(s))
        if not m:
            continue
        a = int(m.group(1)) * 60 + int(m.group(2))
        b = int(m.group(3)) * 60 + int(m.group(4))
        out.append((a, b))
    return out


def _in_windows(minute_of_day: int, windows: list) -> bool:
    for a, b in windows:
        if a <= b:
            if a <= minute_of_day < b:
                return True
        else:  # 跨午夜，例如 23:00-02:00
            if minute_of_day >= a or minute_of_day < b:
                return True
    return False


def _bigrams(text: str) -> set:
    t = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text or "")
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


@dataclass
class GroupMessage:
    """一条群消息（决策所需的最小信息）"""
    msg_id: str = ""
    group_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    text: str = ""
    ts: float = 0.0
    at_me: bool = False
    at_other: bool = False
    is_question: bool = False
    reply_to_id: str = ""
    reply_to_sender: str = ""      # 被引用者的昵称（用于判断"回复我"）


@dataclass
class Decision:
    respond: bool
    reason: str
    score: float
    features: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"respond": self.respond, "reason": self.reason,
                "score": round(self.score, 3), "features": self.features}


class GroupGate:
    """
    群聊接话门禁。

    判定顺序（短路）：
      1. @我 / 回复我        → 必回（忽略一切限制）
      2. 静默时段            → 不回（除非 @ 我，已被 1 拦截）
      3. @别人               → 不回
      4. 太短 / 纯表情        → 不回
      5. 热聊中              → 不回
      6. 冷却期内            → 不回
      7. 开放式提问 / 命中兴趣 / 话题连续 → 计分，过阈值才回
      8. 其他                → 不回（宁漏不多）
    """

    def __init__(self, cfg: dict = None, interest_terms: list = None):
        cfg = dict(DEFAULTS, **(cfg or {}))
        self.cfg = cfg
        self.active_windows = _parse_windows(cfg.get("active_hours"))
        self.quiet_windows = _parse_windows(cfg.get("quiet_hours"))
        self.name_aliases = [a for a in (cfg.get("name_aliases") or []) if a]
        self.interest_terms = [t for t in (interest_terms or []) if len(t) >= 2]

        # 每群状态
        self._recent = {}        # gid -> deque[(ts, sender_id, text)]
        self._last_bot_ts = {}   # gid -> 上次发言时间
        self._last_reply_ts = {} # gid -> 上次"决定回复"时间（冷却用）

    # ---------- 对外 ----------

    def mentions_me(self, text: str) -> bool:
        """称呼检测：英文按词边界，中文按原样（别名本身要足够独特）"""
        t = text or ""
        for alias in self.name_aliases:
            if alias.isascii():
                if re.search(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])",
                             t, flags=re.I):
                    return True
            elif alias in t:
                return True
        return False

    @staticmethod
    def looks_like_question(text: str) -> bool:
        """提问判定：问号结尾，或含常见疑问词"""
        t = (text or "").strip()
        if not t:
            return False
        if t.endswith("?") or t.endswith("？"):
            return True
        return any(k in t for k in ("吗", "呢", "什么", "怎么", "为什么", "哪",
                                    "谁", "多少", "几点", "求推荐", "有没有"))

    def observe(self, msg: GroupMessage, legacy_engage: bool = False,
                bot_name: str = "") -> Decision:
        """观察一条群消息并给出决策（只读状态 + 追加历史）"""
        gid = msg.group_id or "_"
        now = msg.ts or time.time()
        recent = self._recent.setdefault(gid, deque(maxlen=40))

        text = (msg.text or "").strip()
        minute = (time.localtime(now).tm_hour * 60 + time.localtime(now).tm_min)
        in_quiet = _in_windows(minute, self.quiet_windows)
        in_active = _in_windows(minute, self.active_windows)

        # ---- 结构特征 ----
        # at_me：显式 @（适配层传入）或按词边界命中称呼（策略自己判，避免子串误伤）
        at_me = bool(msg.at_me) or self.mentions_me(text)
        # 提问判定：策略自己从文本判断，不依赖适配层传参
        is_question = bool(msg.is_question) or self.looks_like_question(text)
        reply_to_bot = bool(msg.reply_to_sender) and (
            msg.reply_to_sender in self.name_aliases
            or (bot_name and msg.reply_to_sender == bot_name))
        rapid_msgs = [r for r in recent if now - r[0] <= self.cfg["rapid_window_seconds"]]
        rapid = (len(rapid_msgs) + 1 >= self.cfg["rapid_min_msgs"]
                 and len({r[1] for r in rapid_msgs} | {msg.sender_id}) >= 2)
        since_reply = now - self._last_reply_ts.get(gid, 0)
        cooldown = since_reply < self.cfg["cooldown_seconds"]

        # 话题连续性：与最近 5 条的字符 bigram 相似度
        ctx = " ".join(r[2] for r in list(recent)[-5:])
        topic_sim = self._jaccard(_bigrams(text), _bigrams(ctx)) if ctx else 0.0
        # 兴趣命中：消息里出现的熟悉领域词（按字符长度加权命中数）
        interest_hit = [t for t in self.interest_terms if t in text]

        features = {
            "hour": time.localtime(now).tm_hour,
            "in_active": in_active,
            "in_quiet": in_quiet,
            "at_me": at_me,
            "at_me_explicit": bool(msg.at_me),
            "at_other": msg.at_other,
            "reply_to_bot": reply_to_bot,
            "is_question": is_question,
            "rapid": rapid,
            "recent_msgs": len(rapid_msgs) + 1,
            "cooldown": cooldown,
            "topic_sim": round(topic_sim, 3),
            "interest_hits": interest_hit[:5],
            "len": len(text),
            "legacy_engage": legacy_engage,
        }

        decision = self._decide(msg, text, at_me, is_question, in_quiet,
                                reply_to_bot, rapid,
                                cooldown, topic_sim, interest_hit, features)

        # 更新状态
        recent.append((now, msg.sender_id, text))
        if decision.respond:
            self._last_reply_ts[gid] = now
        return decision

    def on_bot_spoke(self, gid: str, text: str, ts: float = None):
        """记录机器人自己发过言（供话题连续性与冷却使用）"""
        ts = ts or time.time()
        self._last_bot_ts[gid] = ts
        self._recent.setdefault(gid, deque(maxlen=40)).append((ts, "__bot__", text or ""))

    # ---------- 内部 ----------

    def _decide(self, msg, text, at_me, is_question, in_quiet, reply_to_bot,
                rapid, cooldown, topic_sim, interest_hit, features) -> Decision:
        cfg = self.cfg

        # 1. 明确对我：无视一切限制
        if at_me:
            return Decision(True, "at_me", 1.0, features)
        # 2. 回复我：也算明确对话
        if reply_to_bot:
            return Decision(True, "reply_to_me", 0.95, features)
        # 3. 静默时段
        if in_quiet:
            return Decision(False, "quiet_hours", 0.0, features)
        # 4. 明确对别人说
        if msg.at_other:
            return Decision(False, "addressed_to_other", 0.02, features)
        # 5. 太短 / 纯符号
        if len(re.sub(r"\W", "", text)) < cfg["min_len"]:
            return Decision(False, "too_short", 0.05, features)
        # 6. 热聊中，别插
        if rapid:
            return Decision(False, "hot_chat", 0.10, features)
        # 7. 冷却期
        if cooldown:
            return Decision(False, "cooldown", 0.15, features)

        # 8. 综合计分（宁漏不多：默认不接，靠信号把分数顶过阈值）
        score = 0.0
        if is_question:
            score += cfg["question_bonus"]
        if interest_hit:
            score += cfg["interest_bonus"]
        score += cfg["topic_weight"] * topic_sim

        reason = "no_signal"
        if score >= cfg["threshold"]:
            if is_question:
                reason = "open_question"
            elif interest_hit:
                reason = "interest_match"
            else:
                reason = "topic_continuity"
            return Decision(True, reason, min(1.0, score), features)
        return Decision(False, reason, score, features)

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    # ---------- 诊断 ----------

    def stats(self, gid: str = None) -> dict:
        if gid:
            return {"recent": len(self._recent.get(gid, [])),
                    "last_reply_ago": round(time.time() - self._last_reply_ts.get(gid, 0), 1)}
        return {"groups": len(self._recent)}


def load_interest_terms(facts_path: str = None) -> list:
    """从 facts yaml 里抽取她熟悉的领域词，用作兴趣命中检测"""
    try:
        import yaml
        from pathlib import Path
        if facts_path is None:
            base = Path(__file__).resolve().parent.parent
            facts_path = str(base / "config" / "persona_prompts" / "shelwell_facts.yaml")
        with open(facts_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        terms = set()
        for value in data.values():
            if not isinstance(value, list):
                continue
            for item in value:
                # 条目形如 "明日方舟：玩了一年多，主推能天使"
                head = re.split(r"[：:,，。；;]", str(item))[0].strip()
                for piece in re.split(r"[、/（）()]", head):
                    piece = piece.strip()
                    if 2 <= len(piece) <= 8:
                        terms.add(piece)
        return sorted(terms)
    except Exception as e:
        print(f"[GroupPolicy] 兴趣词加载失败: {e}")
        return []
