# backend/memory/models.py
# 职责：L3 记忆系统数据模型——情景记忆、语义记忆、状态枚举
# 约定：所有时间戳为 UTC ISO 8601 字符串；所有模型提供 to_dict / from_dict 供 JSONL 与 ChromaDB 序列化

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid


def now_iso() -> str:
    """当前 UTC 时间，ISO 8601，秒级精度"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gen_id(prefix: str) -> str:
    """生成带前缀的唯一 ID，如 epi_a1b2c3d4e5f6"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class FactState(str, Enum):
    """语义事实状态"""
    ACTIVE = "active"        # 正常参与检索
    DORMANT = "dormant"      # 沉睡，不参与检索，不删除
    ARCHIVED = "archived"    # 归档到冷库，可恢复
    SUPERSEDED = "superseded"  # 被新版本取代，保留历史


@dataclass
class MemoryEvent:
    """情景记忆：一轮完整对话的原文归档"""

    id: str
    timestamp: str
    session_id: str
    user_msg: str
    ai_msg: str
    # 归属：谁在哪个场景说的
    speaker_id: str = ""       # 发言者 QQ 号
    speaker_name: str = ""     # 发言者昵称/群名片（提炼事实时用作主语）
    channel_type: str = "private"  # private / group
    channel_id: str = ""       # 私聊=用户QQ号，群聊=群号
    score: float = 0.0
    emotion: str = ""
    # 索引状态：pending / indexed / failed
    index_status: str = "pending"
    # 是否已被语义提炼消费
    consolidated: bool = False

    @staticmethod
    def create(session_id: str, user_msg: str, ai_msg: str,
               score: float = 0.0, emotion: str = "",
               speaker_id: str = "", channel_type: str = "private",
               channel_id: str = "", speaker_name: str = "") -> "MemoryEvent":
        # 未显式传归属时，从 session_id 推断（兼容旧调用）
        if not speaker_id:
            parsed = parse_session(session_id)
            speaker_id = parsed["speaker_id"]
            channel_type = parsed["channel_type"]
            channel_id = parsed["channel_id"]
        return MemoryEvent(
            id=gen_id("epi"),
            timestamp=now_iso(),
            session_id=session_id,
            user_msg=user_msg,
            ai_msg=ai_msg,
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            channel_type=channel_type,
            channel_id=channel_id,
            score=score,
            emotion=emotion,
        )

    @property
    def combined_text(self) -> str:
        """用于向量化的合并文本"""
        return f"用户：{self.user_msg}\nShelwell：{self.ai_msg}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryEvent":
        d = dict(d)
        # 兼容旧 JSONL 字段（user/assistant）
        if "user" in d and "user_msg" not in d:
            d["user_msg"] = d.pop("user")
            d["ai_msg"] = d.pop("assistant", "")
        # 兼容旧数据：无归属字段时从 session_id 推断
        if not d.get("speaker_id") or not d.get("channel_id"):
            parsed = parse_session(d.get("session_id", "default"))
            d.setdefault("speaker_id", parsed["speaker_id"])
            d.setdefault("channel_type", parsed["channel_type"])
            d.setdefault("channel_id", parsed["channel_id"])
            if not d["speaker_id"]:
                d["speaker_id"] = parsed["speaker_id"]
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


def parse_session(session_id: str) -> dict:
    """
    从 session_id 解析归属信息，兼容旧格式。
    qq:2672768829       -> speaker=2672768829, type=private, channel=2672768829
    qqg:12345:2672768829 -> speaker=2672768829, type=group,   channel=12345
    其他（default 等）    -> speaker=session_id 原值, type=private
    """
    sid = session_id or "default"
    if sid.startswith("qqg:"):
        parts = sid.split(":")
        if len(parts) >= 3:
            return {"speaker_id": parts[2], "channel_type": "group", "channel_id": parts[1]}
    elif sid.startswith("qq:"):
        parts = sid.split(":")
        if len(parts) >= 2:
            return {"speaker_id": parts[1], "channel_type": "private", "channel_id": parts[1]}
    return {"speaker_id": sid, "channel_type": "private", "channel_id": sid}


@dataclass
class SemanticFact:
    """语义记忆：从情景记忆中提炼出的可引用事实"""

    id: str
    fact: str
    subject: str = ""
    predicate: str = ""
    object: str = ""
    score: float = 0.0
    strength: float = 1.0
    confidence: float = 0.5
    detail_level: float = 1.0
    # 权重体系（借鉴 qq-dsh-bridge）
    tag: str = "misc"                 # identity/agreement/emotion/preference/meme/misc
    hits: int = 0                     # 被重新提到的次数
    base_weight: float = 0.0          # 基础分，0 表示按 tag 自动取
    archived: bool = False            # 权重跌破归档线后置 true，不删除
    last_hit: str = ""                # 最后一次命中时间（衰减从这里算）
    source_ids: list = field(default_factory=list)
    valid_from: str = ""
    valid_to: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_activated_at: str = ""
    state: str = FactState.ACTIVE.value
    extractor_version: str = "v1"

    @staticmethod
    def create(fact: str, source_ids: list, score: float = 0.0,
               subject: str = "", predicate: str = "", object: str = "",
               tag: str = "misc") -> "SemanticFact":
        ts = now_iso()
        # 按 tag 取基础权重
        from backend.memory.forgetting import TAG_WEIGHTS
        base = TAG_WEIGHTS.get(tag, TAG_WEIGHTS["misc"])
        # 初始置信度：来源数越多越可信（最多 +0.15），三元组完整 +0.15
        src_n = min(len(source_ids), 3)
        triple_complete = bool(subject and predicate and object)
        conf = 0.4 + 0.05 * src_n + (0.15 if triple_complete else 0.0)
        conf = round(min(1.0, conf), 2)
        return SemanticFact(
            id=gen_id("sem"),
            fact=fact,
            subject=subject,
            predicate=predicate,
            object=object,
            score=score,
            strength=1.0,
            confidence=conf,
            detail_level=1.0,
            tag=tag if tag in TAG_WEIGHTS else "misc",
            base_weight=base,
            hits=0,
            archived=False,
            last_hit=ts,
            source_ids=list(source_ids),
            valid_from=ts,
            created_at=ts,
            updated_at=ts,
            last_activated_at=ts,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticFact":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    def touch(self):
        """被检索命中时刷新激活时间"""
        self.last_activated_at = now_iso()
        self.updated_at = self.last_activated_at