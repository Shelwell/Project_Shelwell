# backend/relationship.py
# 职责：L3 能力层——发言人关系等级（确定性判定，不交给模型）
#
# 设计约束（2026-11-05）：
#   1. 等级只由代码按「聊天条数 + 认识时长」计算，模型不得自行推断或升格
#   2. 会话里第一次出现的人，从「陌生」开始；关系随真实互动累积，重启不丢
#   3. 条数、时长各自套档后取较低档（短板决定关系）；「信任」要求两者同时
#      超过阈值，「依赖」还必须是唯一指定对象
#   4. 数据存 SQLite（memory/index/relationships.sqlite3），JSONL 归档仍是真相源，
#      首次建库时从归档回填，避免升级后老朋友被当成陌生人

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config_loader import get

# ---------- 等级 ----------

LEVELS = ("stranger", "acquaintance", "known", "trust", "dependent")
LEVEL_CN = {
    "stranger": "陌生",
    "acquaintance": "认识",
    "known": "了解",
    "trust": "信任",
    "dependent": "依赖",
}
LEVEL_INDEX = {k: i for i, k in enumerate(LEVELS)}

# 默认阈值：条数（条）/ 时间（小时）
DEFAULT_THRESHOLDS = {
    "stranger_max_msgs": 50,
    "stranger_max_hours": 120,      # 5 天
    "acquaint_max_msgs": 300,
    "acquaint_max_hours": 360,      # 15 天
    "known_max_msgs": 1000,
    "known_max_hours": 1080,        # 45 天；超过即进入「信任」档
    "depend_min_msgs": 3000,
    "depend_min_hours": 2160,       # 90 天
}

# 每级允许的态度（控制台展示用，真正的措辞在 persona yaml）
LEVEL_ATTITUDE = {
    "stranger": "客气、简短、保持距离；不傲娇、不毒舌、不玩梗",
    "acquaintance": "放松一些，可以正常闲聊；仍不傲娇、不毒舌、不玩梗",
    "known": "朋友口吻，可以轻松开玩笑但不损人；不傲娇、不毒舌、不玩梗",
    "trust": "私聊可傲娇、毒舌、玩梗；群聊仍收着",
    "dependent": "私聊和群聊都可傲娇、毒舌、玩梗；可以示弱、说自己的事",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(ts=None) -> str:
    """把 datetime / epoch / ISO 字符串统一成 UTC ISO 秒级字符串"""
    if ts is None:
        dt = _now()
    elif isinstance(ts, datetime):
        dt = ts
    elif isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        dt = _parse_iso(str(ts))
        if dt is None:
            dt = _now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(text: str) -> Optional[datetime]:
    s = (text or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _hours_since(first_seen: str, now: datetime = None) -> float:
    dt = _parse_iso(first_seen)
    if dt is None:
        return 0.0
    delta = (now or _now()) - dt
    return max(0.0, delta.total_seconds() / 3600.0)


def _earlier(a: str, b: str) -> str:
    """两个 ISO 时间取较早者；解析失败时退回字符串比较"""
    da, db = _parse_iso(a), _parse_iso(b)
    if da is not None and db is not None:
        return a if da <= db else b
    return a if str(a) <= str(b) else b


def _later(a: str, b: str) -> str:
    """两个 ISO 时间取较晚者"""
    da, db = _parse_iso(a), _parse_iso(b)
    if da is not None and db is not None:
        return a if da >= db else b
    return a if str(a) >= str(b) else b


def _tier_by_msgs(count: int, t: dict) -> int:
    if count > int(t["depend_min_msgs"]):
        return 4
    if count > int(t["known_max_msgs"]):
        return 3
    if count > int(t["acquaint_max_msgs"]):
        return 2
    if count > int(t["stranger_max_msgs"]):
        return 1
    return 0


def _tier_by_hours(hours: float, t: dict) -> int:
    if hours > int(t["depend_min_hours"]):
        return 4
    if hours > int(t["known_max_hours"]):
        return 3
    if hours > int(t["acquaint_max_hours"]):
        return 2
    if hours > int(t["stranger_max_hours"]):
        return 1
    return 0


def compute_level(msg_count: int, hours: float, is_dependent: bool = False,
                  thresholds: dict = None) -> str:
    """
    计算关系等级（纯函数，便于测试）。

    规则：
      - 条数、时长各自套档，最终取**较低**的那一档（短板决定关系）
      - 因此「信任」要求条数、时长同时超过阈值；只满足一边时由低的一边决定
      - 「依赖」要求两个维度都超过 depend 阈值，且必须是唯一指定的对象
      - 未指定的对象最高只能到「信任」，不会出现「依赖」
    """
    t = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        t.update(thresholds)
    count = max(0, int(msg_count or 0))
    hours = max(0.0, float(hours or 0.0))

    tier = min(_tier_by_msgs(count, t), _tier_by_hours(hours, t))
    if tier == 4 and not is_dependent:
        tier = 3
    return LEVELS[tier]


def human_hours(hours: float) -> str:
    """把小时数写成人话：不到 1 小时 / N 小时 / N 天"""
    h = max(0.0, float(hours or 0.0))
    if h < 1:
        return "不到 1 小时"
    if h < 48:
        return "%d 小时" % int(h)
    return "%d 天" % int(h // 24)


def tsundere_policy(level: str, is_group: bool) -> tuple:
    """
    当前场景是否允许傲娇/毒舌/玩梗。
    返回 (是否允许, 补充说明)；群聊里只有「依赖」解锁。
    """
    idx = LEVEL_INDEX.get(level, 0)
    if level not in ("trust", "dependent"):
        return "禁止", ""
    if is_group and level == "trust":
        return "禁止", "（群聊只对依赖解锁）"
    if is_group and level == "dependent":
        return "允许", "（群聊也可）"
    return "允许", ""


@dataclass
class Relationship:
    """某个发言人的关系快照（每次读取时按当前时间计算）"""

    speaker_id: str
    display_name: str = ""
    first_seen: str = ""
    last_seen: str = ""
    msg_count: int = 0
    at_count: int = 0
    reply_count: int = 0
    channel_type: str = ""
    channel_id: str = ""
    hours: float = 0.0
    level: str = "stranger"
    dependent: bool = False

    @property
    def display(self) -> str:
        return self.display_name or self.speaker_id or "对方"

    @property
    def level_cn(self) -> str:
        return LEVEL_CN.get(self.level, self.level)

    def to_dict(self) -> dict:
        return {
            "speaker_id": self.speaker_id,
            "display_name": self.display_name,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "msg_count": self.msg_count,
            "at_count": self.at_count,
            "reply_count": self.reply_count,
            "channel_type": self.channel_type,
            "channel_id": self.channel_id,
            "hours": round(self.hours, 2),
            "hours_text": human_hours(self.hours),
            "level": self.level,
            "level_cn": self.level_cn,
            "dependent": self.dependent,
            "attitude": LEVEL_ATTITUDE.get(self.level, ""),
        }


class RelationshipStore:
    """关系等级存储：SQLite 持久化，按发言人聚合"""

    def __init__(self, db_path: str = None, dependent_id: str = None,
                 thresholds: dict = None, enabled: bool = None):
        cfg = get("relationship", {}) or {}
        base = Path(__file__).resolve().parent.parent
        if db_path is None:
            db_path = str(cfg.get("db_path") or "memory/index/relationships.sqlite3")
            p = Path(db_path)
            if not p.is_absolute():
                db_path = str(base / p)
        self.db_path = os.path.abspath(str(db_path))
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)

        self.thresholds = dict(DEFAULT_THRESHOLDS)
        self.thresholds.update(cfg.get("thresholds") or {})
        if thresholds:
            self.thresholds.update(thresholds)

        self.enabled = bool(cfg.get("enabled", True)) if enabled is None else bool(enabled)
        self._explicit_dependent = dependent_id
        self._init_db()

    # ---------- SQLite ----------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    @contextmanager
    def _db(self):
        """短连接：提交/回滚后一定关闭，避免 Windows 下句柄泄漏（WinError 32）"""
        con = self._connect()
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self):
        with self._db() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                    speaker_id   TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    first_seen   TEXT NOT NULL,
                    last_seen    TEXT NOT NULL,
                    msg_count    INTEGER NOT NULL DEFAULT 0,
                    at_count     INTEGER NOT NULL DEFAULT 0,
                    reply_count  INTEGER NOT NULL DEFAULT 0,
                    channel_type TEXT NOT NULL DEFAULT '',
                    channel_id   TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
            """)

    def _meta(self, key: str) -> str:
        with self._db() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def _set_meta(self, key: str, value: str):
        with self._db() as con:
            con.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))

    # ---------- 唯一指定对象（依赖） ----------

    def dependent_id(self) -> str:
        if self._explicit_dependent is not None:
            return str(self._explicit_dependent or "").strip()
        try:
            from backend.runtime_config import get_runtime
            v = get_runtime().get("relationship.dependent_id", "")
            if v:
                return str(v).strip()
        except Exception:
            pass
        return str(get("relationship.dependent_id", "") or "").strip()

    def is_dependent(self, speaker_id: str) -> bool:
        dep = self.dependent_id()
        return bool(dep) and str(speaker_id or "").strip() == dep

    # ---------- 写入 ----------

    def observe(self, speaker_id: str, display_name: str = "",
                channel_type: str = "", channel_id: str = "",
                at_me: bool = False, reply_to_bot: bool = False,
                ts=None) -> Optional[Relationship]:
        """记录一条该发言人的消息（由 MemoryService 在归档时调用）"""
        sid = str(speaker_id or "").strip()
        if not sid:
            return None
        ts_iso = _to_iso(ts)
        name = str(display_name or "").strip()
        with self._db() as con:
            row = con.execute(
                "SELECT first_seen FROM relationships WHERE speaker_id=?",
                (sid,)).fetchone()
            if row:
                first = _earlier(str(row["first_seen"] or ts_iso), ts_iso)
                con.execute("""
                    UPDATE relationships SET
                        display_name = CASE WHEN ? <> '' THEN ? ELSE display_name END,
                        first_seen = ?, last_seen = ?,
                        msg_count = msg_count + 1,
                        at_count = at_count + ?,
                        reply_count = reply_count + ?,
                        channel_type = CASE WHEN ? <> '' THEN ? ELSE channel_type END,
                        channel_id = CASE WHEN ? <> '' THEN ? ELSE channel_id END,
                        updated_at = ?
                    WHERE speaker_id = ?
                """, (name, name, first, ts_iso, int(bool(at_me)),
                      int(bool(reply_to_bot)), str(channel_type or ""),
                      str(channel_type or ""), str(channel_id or ""),
                      str(channel_id or ""), _to_iso(), sid))
            else:
                con.execute("""
                    INSERT INTO relationships
                        (speaker_id, display_name, first_seen, last_seen,
                         msg_count, at_count, reply_count, channel_type,
                         channel_id, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """, (sid, name, ts_iso, ts_iso, int(bool(at_me)),
                      int(bool(reply_to_bot)), str(channel_type or ""),
                      str(channel_id or ""), _to_iso()))
        return self.get(sid)

    def delete(self, speaker_id: str) -> bool:
        sid = str(speaker_id or "").strip()
        if not sid:
            return False
        with self._db() as con:
            cur = con.execute("DELETE FROM relationships WHERE speaker_id=?", (sid,))
            return cur.rowcount > 0

    def clear_all(self) -> int:
        with self._db() as con:
            cur = con.execute("DELETE FROM relationships")
            con.execute("DELETE FROM meta")
            # 标记已回填，避免下次打开时又从归档把记录建回来
            con.execute("INSERT INTO meta(key, value) VALUES('backfilled', '1')")
            return cur.rowcount

    # ---------- 读取 ----------

    def _row_to_rel(self, row: sqlite3.Row, now: datetime = None,
                    display_name: str = "") -> Relationship:
        hours = _hours_since(row["first_seen"], now)
        count = int(row["msg_count"] or 0)
        dep = self.is_dependent(row["speaker_id"])
        return Relationship(
            speaker_id=str(row["speaker_id"]),
            display_name=str(row["display_name"] or display_name or ""),
            first_seen=str(row["first_seen"] or ""),
            last_seen=str(row["last_seen"] or ""),
            msg_count=count,
            at_count=int(row["at_count"] or 0),
            reply_count=int(row["reply_count"] or 0),
            channel_type=str(row["channel_type"] or ""),
            channel_id=str(row["channel_id"] or ""),
            hours=hours,
            level=compute_level(count, hours, dep, self.thresholds),
            dependent=dep,
        )

    def get(self, speaker_id: str, display_name: str = "",
            now: datetime = None) -> Optional[Relationship]:
        sid = str(speaker_id or "").strip()
        if not sid:
            return None
        with self._db() as con:
            row = con.execute(
                "SELECT * FROM relationships WHERE speaker_id=?", (sid,)).fetchone()
        if row is None:
            dep = self.is_dependent(sid)
            return Relationship(
                speaker_id=sid, display_name=display_name or "",
                hours=0.0, msg_count=0,
                level=compute_level(0, 0.0, dep, self.thresholds),
                dependent=dep)
        return self._row_to_rel(row, now=now, display_name=display_name)

    def level_of(self, speaker_id: str, now: datetime = None) -> str:
        rel = self.get(speaker_id, now=now)
        return rel.level if rel else "stranger"

    def all(self, now: datetime = None) -> list:
        """列出所有记录，按等级从高到低、消息数从多到少排序"""
        with self._db() as con:
            rows = con.execute("SELECT * FROM relationships").fetchall()
        out = [self._row_to_rel(r, now=now) for r in rows]
        out.sort(key=lambda r: (LEVEL_INDEX.get(r.level, 0), r.msg_count,
                                r.last_seen), reverse=True)
        return out

    def stats(self) -> dict:
        with self._db() as con:
            total = con.execute("SELECT COUNT(*) AS n FROM relationships").fetchone()["n"]
            rows = con.execute(
                "SELECT msg_count FROM relationships").fetchall()
        return {"total": int(total),
                "messages": sum(int(r["msg_count"] or 0) for r in rows),
                "dependent_id": self.dependent_id()}

    # ---------- 一次性回填 ----------

    def ensure_backfill(self, archive) -> int:
        """
        首次建库时从 JSONL 归档回填，避免升级后把老朋友当成陌生人。
        只跑一次（meta.backfilled），已有实时数据时直接标记完成。
        """
        if not self.enabled:
            return 0
        if self._meta("backfilled") == "1":
            return 0
        with self._db() as con:
            existing = con.execute(
                "SELECT COUNT(*) AS n FROM relationships").fetchone()["n"]
        if existing:
            self._set_meta("backfilled", "1")
            return 0

        agg = {}
        try:
            for ev in archive._iter_all():
                sid = str(getattr(ev, "speaker_id", "") or "").strip()
                if not sid:
                    continue
                ts = str(getattr(ev, "timestamp", "") or "")
                if not ts:
                    continue
                item = agg.get(sid)
                if item is None:
                    agg[sid] = {
                        "count": 1, "first": ts, "last": ts,
                        "name": getattr(ev, "speaker_name", "") or "",
                        "ct": getattr(ev, "channel_type", "") or "",
                        "ci": getattr(ev, "channel_id", "") or "",
                    }
                    continue
                item["count"] += 1
                item["first"] = _earlier(item["first"], ts)
                last = _later(item["last"], ts)
                if last != item["last"]:
                    item["last"] = last
                    item["ct"] = getattr(ev, "channel_type", "") or item["ct"]
                    item["ci"] = getattr(ev, "channel_id", "") or item["ci"]
                if getattr(ev, "speaker_name", ""):
                    item["name"] = ev.speaker_name
        except Exception as e:
            print(f"[Relationship] 归档回填读取失败: {e}")
            return 0

        if agg:
            with self._db() as con:
                for sid, a in agg.items():
                    con.execute("""
                        INSERT OR IGNORE INTO relationships
                            (speaker_id, display_name, first_seen, last_seen,
                             msg_count, at_count, reply_count, channel_type,
                             channel_id, updated_at)
                        VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
                    """, (sid, a["name"], a["first"], a["last"], a["count"],
                          a["ct"], a["ci"], _to_iso()))
        self._set_meta("backfilled", "1")
        if agg:
            print("[Relationship] 已从归档回填 %d 位发言人" % len(agg))
        return len(agg)


def render_relationship_block(rel: Relationship, channel_type: str = "private") -> str:
    """生成本轮注入的【关系】段（动态内容，放 user 尾部，不破坏前缀缓存）"""
    if rel is None:
        return ""
    # 昵称来自 QQ，压成一行并限长，避免把换行/超长文本带进提示词
    name = str(rel.display or "对方").replace("\n", " ").replace("\r", " ").strip()[:24]
    if not name:
        name = "对方"
    if rel.msg_count <= 0:
        stats = "初次接触"
    else:
        stats = "认识 %s，聊过 %d 条" % (human_hours(rel.hours), rel.msg_count)
    is_group = (channel_type == "group")
    allow, note = tsundere_policy(rel.level, is_group)
    return (
        "【关系｜系统判定，勿自行升降】当前说话人：%s｜%s｜等级：%s"
        "｜傲娇/毒舌/玩梗：%s%s"
        % (name, stats, rel.level_cn, allow, note)
    )
