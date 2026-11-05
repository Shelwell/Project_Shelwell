# backend/memory/forgetting.py
# 职责：语义事实的权重计算、衰减、归档、注入选择
# 设计借鉴 qq-dsh-bridge：权重 = 基础分 + 命中回补 - 时间衰减
# 核心原则：只归档不删除；排序靠权重不靠时间

from datetime import datetime, timezone

# ---------- 常量 ----------

# tag → 基础权重（分值越大越该被记住）
TAG_WEIGHTS = {
    "identity": 3.0,     # 身份：我是谁、你是谁、什么关系
    "agreement": 3.0,    # 约定：答应过的事、规则
    "emotion": 3.0,      # 情绪：强烈喜恶、闹过别扭
    "preference": 2.5,   # 偏好：喜欢/讨厌什么
    "meme": 1.6,         # 梗：群里的玩笑、口头禅
    "misc": 1.3,         # 琐事：其余
}

DECAY_PER_DAY = 0.1        # 每 3 天掉 0.1
DECAY_INTERVAL_DAYS = 3
HIT_BONUS = 0.5            # 每次被提到 +0.5
HIT_BONUS_CAP = 1.5        # 命中回补上限
ARCHIVE_THRESHOLD = 1.0    # 归档线，低于它不注入
INJECT_MAX = 30            # 单次最多注入条数
INJECT_MAX_CHARS = 900     # 注入文本字符预算

DAY_MS = 86400000


# ---------- 时间工具 ----------

def _now_ms() -> float:
    return datetime.now(timezone.utc).timestamp() * 1000


def _parse_iso_ms(ts: str) -> float:
    """ISO 字符串转毫秒时间戳，失败返回 0"""
    if not ts:
        return 0
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000
    except Exception:
        return 0


# ---------- 权重计算 ----------

def weight_of(fact, now: float = None) -> float:
    """
    当前有效权重 = 基础分 + 命中回补 - 时间衰减。
    fact 需有 base_weight/tag/hits/last_hit(或 last_activated_at) 属性。
    """
    if now is None:
        now = _now_ms()

    # 基础分：优先用 base_weight，为 0 时按 tag 取
    base = getattr(fact, "base_weight", 0) or 0
    if base <= 0:
        tag = getattr(fact, "tag", "misc") or "misc"
        base = TAG_WEIGHTS.get(tag, TAG_WEIGHTS["misc"])

    # 命中回补
    hits = getattr(fact, "hits", 0) or 0
    bonus = min(HIT_BONUS_CAP, hits * HIT_BONUS)

    # 时间衰减：从 last_hit 或 last_activated_at 算
    last = getattr(fact, "last_hit", "") or getattr(fact, "last_activated_at", "")
    from_ms = _parse_iso_ms(last) if last else _parse_iso_ms(getattr(fact, "created_at", ""))
    if from_ms <= 0:
        from_ms = now
    days = max(0.0, (now - from_ms) / DAY_MS)
    decay = DECAY_PER_DAY * int(days // DECAY_INTERVAL_DAYS)

    return round(base + bonus - decay, 3)


def should_archive(fact, now: float = None) -> bool:
    """是否跌破归档线"""
    return weight_of(fact, now) < ARCHIVE_THRESHOLD


# ---------- 注入选择 ----------

def select_for_injection(facts: list, now: float = None,
                         max_count: int = INJECT_MAX,
                         max_chars: int = INJECT_MAX_CHARS) -> list:
    """
    从候选事实里按权重降序选出要注入的。
    过滤已归档、权重低于线的；受条数和字符预算双重约束。
    返回 fact 列表（已带 weight 临时属性）。
    """
    if not facts:
        return []
    if now is None:
        now = _now_ms()

    scored = []
    for f in facts:
        if getattr(f, "archived", False):
            continue
        w = weight_of(f, now)
        if w < ARCHIVE_THRESHOLD:
            continue
        f._weight = w
        scored.append(f)

    scored.sort(key=lambda x: (x._weight, getattr(x, "last_hit", "")), reverse=True)

    picked = []
    used = 0
    for f in scored[:max_count]:
        text = getattr(f, "fact", "")
        line_len = len(text) + 3  # "- " + text + "\n"
        if used + line_len > max_chars:
            break
        picked.append(f)
        used += line_len
    return picked


def format_injection(picked: list, template: dict = None) -> str:
    """按人格 YAML 模板格式化选中的事实。"""
    if not picked:
        return ""
    template = template or {}
    header = str(template.get("header", "")).format(count=len(picked))
    prefix = str(template.get("line_prefix", ""))
    lines = []
    for f in picked:
        text = getattr(f, "fact", "")
        subject = str(getattr(f, "subject", "") or "").strip()
        # 旧事实主语可能是用户昵称（甚至和角色英文名重名）；注入时统一成“对方”
        if subject and text.startswith(subject):
            text = "对方" + text[len(subject):]
        lines.append(prefix + text)
    return "\n".join(part for part in (header, "\n".join(lines)) if part)


# ---------- 统计 ----------

def summarize(facts: list, now: float = None) -> dict:
    """给控制台看的一览"""
    if now is None:
        now = _now_ms()
    if not facts:
        return {"total": 0, "live": 0, "archived": 0,
                "top_weight": "0.00", "bottom_weight": "0.00"}
    live = [f for f in facts if not getattr(f, "archived", False)]
    weights = [weight_of(f, now) for f in live]
    return {
        "total": len(facts),
        "live": len(live),
        "archived": len(facts) - len(live),
        "top_weight": "%.2f" % max(weights) if weights else "0.00",
        "bottom_weight": "%.2f" % min(weights) if weights else "0.00",
    }
