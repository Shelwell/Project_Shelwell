# adapters/qq_bot/engagement.py
# 职责：群聊自动接话的热度状态机
# 设计：热度连续半衰期衰减 + 双阈值（冷启动/对话中），接话后进入短对话窗口

import time
from collections import deque


class EngagementTracker:
    """按群维护参与热度的状态机"""

    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled", False))
        self.heat_threshold = float(cfg.get("heat_threshold", 7.0))
        self.conversation_threshold = float(cfg.get("conversation_threshold", 4.0))
        self.conversation_window_minutes = float(cfg.get("conversation_window_minutes", 3))
        self.half_life_minutes = float(cfg.get("half_life_minutes", 60))
        self.max_heat = float(cfg.get("max_heat", 10.0))
        self.heat_at_me = float(cfg.get("heat_at_me", 10.0))
        self.heat_question = float(cfg.get("heat_question", 1.0))
        self.heat_topic = float(cfg.get("heat_topic", 2.0))
        self.heat_normal = float(cfg.get("heat_normal", 0.3))
        self.heat_reply_to_me = float(cfg.get("heat_reply_to_me", 0.3))
        self.heat_rapid = float(cfg.get("heat_rapid", -1.0))
        self.skip_short_len = int(cfg.get("skip_short_len", 4))

        # 状态：每群一份
        # heat 存的是"上次结算时刻的值"，读取时按半衰期折算
        self._heat = {}           # gid -> float
        self._last_settle = {}    # gid -> float（上次结算时间戳）
        self._conv_until = {}     # gid -> float（对话窗口截止时间）
        self._last_engage = {}    # gid -> float（上次接话时间）
        self._skip_next = {}      # gid -> bool（接话后跳过下一条，防连发）
        self._recent = {}         # gid -> deque[(sender_id, ts)]

    # ---------- 半衰期结算 ----------

    def _settle(self, gid: str, now: float) -> float:
        """把热度按经过时间折算到当前时刻，返回当前热度"""
        if gid not in self._heat:
            self._heat[gid] = 0.0
            self._last_settle[gid] = now
            return 0.0
        last = self._last_settle.get(gid, now)
        elapsed_min = max(0.0, (now - last) / 60.0)
        if elapsed_min > 0 and self.half_life_minutes > 0:
            # heat × 2^(-elapsed/half_life)
            factor = 2 ** (-elapsed_min / self.half_life_minutes)
            self._heat[gid] = self._heat[gid] * factor
        self._last_settle[gid] = now
        return self._heat[gid]

    def _add_heat(self, gid: str, delta: float, now: float):
        """加热度（先结算再累加，封顶）"""
        cur = self._settle(gid, now)
        self._heat[gid] = max(0.0, min(self.max_heat, cur + delta))

    def _get_heat(self, gid: str, now: float = None) -> float:
        if now is None:
            now = time.time()
        return self._settle(gid, now)

    def _in_conversation(self, gid: str, now: float) -> bool:
        """当前是否在接话后的对话窗口内"""
        return now < self._conv_until.get(gid, 0)

    def _is_rapid_exchange(self, gid: str, sender_id: str, now: float) -> bool:
        """最近 30 秒内两人以上交替发言 → 群在热聊，插入会打扰"""
        q = self._recent.get(gid)
        if not q or len(q) < 2:
            return False
        recent = [(s, t) for (s, t) in q if now - t <= 30]
        if len(recent) < 2:
            return False
        if len({s for s, _ in recent}) < 2:
            return False
        return sender_id != recent[-1][0]

    # ---------- 对外接口 ----------

    def observe(self, gid: str, sender_id: str, text: str,
                is_at_me: bool = False, is_question: bool = False) -> float:
        """观察一条群消息，更新热度，返回更新后热度"""
        if not self.enabled:
            return 0.0
        now = time.time()

        # 维护最近发言队列（保留 60 秒）
        q = self._recent.setdefault(gid, deque(maxlen=20))
        q.append((sender_id, now))
        while q and now - q[0][1] > 60:
            q.popleft()

        # 短消息跳过
        if len(text or "") < self.skip_short_len:
            self._settle(gid, now)
            return self._heat.get(gid, 0.0)

        # @我 或叫名字：直接拉满
        if is_at_me:
            self._heat[gid] = self.heat_at_me
            self._last_settle[gid] = now
            return self._heat[gid]

        # 结算后按类型累加
        cur = self._settle(gid, now)
        delta = 0.0

        if self._in_conversation(gid, now):
            # 对话中：用户的消息算"回复我"
            delta += self.heat_reply_to_me
        else:
            delta += self.heat_normal

        if is_question:
            delta += self.heat_question
        if self._is_rapid_exchange(gid, sender_id, now):
            delta += self.heat_rapid

        self._heat[gid] = max(0.0, min(self.max_heat, cur + delta))
        return self._heat[gid]

    def should_engage(self, gid: str, is_at_me: bool = False) -> bool:
        """
        判断是否该接话。
        - @我：无视一切限制直接接
        - 接话后的下一条消息：跳过（防连发）
        - 对话窗口中：阈值 4.0
        - 冷启动：阈值 7.0
        """
        if not self.enabled:
            return False
        if is_at_me:
            return True

        now = time.time()

        # 防连发：接话后跳过一次
        if self._skip_next.get(gid):
            self._skip_next[gid] = False
            return False

        heat = self._get_heat(gid, now)
        if self._in_conversation(gid, now):
            return heat >= self.conversation_threshold
        return heat >= self.heat_threshold

    def on_engaged(self, gid: str):
        """接话后：不减半，开启对话窗口，跳过下一条"""
        now = time.time()
        self._settle(gid, now)
        self._conv_until[gid] = now + self.conversation_window_minutes * 60
        self._last_engage[gid] = now
        self._skip_next[gid] = True

    def suggest_keep_alive(self, gid: str) -> str:
        """
        按当前热度建议 12B 模型的驻留时长，供 Ollama keep_alive 使用。
        - 对话窗口内 或 热度高于阈值：常驻（30m），避免反复加载
        - 热度低：快速释放（2m），让出显存
        enabled 关闭时返回 None，走 provider 默认配置。
        """
        if not self.enabled:
            return None
        now = time.time()
        heat = self._get_heat(gid, now)
        if self._in_conversation(gid, now) or heat >= self.heat_threshold:
            return "30m"
        return "2m"

    def stats(self, gid: str = None) -> dict:
        now = time.time()
        if gid:
            return {
                "gid": gid,
                "heat": round(self._get_heat(gid, now), 2),
                "in_conversation": self._in_conversation(gid, now),
                "conv_until_in_min": round(max(0, (self._conv_until.get(gid, 0) - now) / 60), 1),
            }
        return {"groups": {g: self.stats(g) for g in self._heat}}