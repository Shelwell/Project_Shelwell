# scripts/test_relationship.py
# 职责：关系等级判定回归测试——阈值边界、信任/依赖门槛、注入位置、归档回填
# 用法：.venv\Scripts\python.exe scripts\test_relationship.py

import sys
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


T = {
    "stranger_max_msgs": 0, "stranger_max_hours": 24,
    "acquaint_max_msgs": 300, "acquaint_max_hours": 120,
    "known_max_msgs": 1000, "known_max_hours": 360,
    "depend_min_msgs": 3000, "depend_min_hours": 1080,
}

from backend.relationship import (
    compute_level, tsundere_policy, RelationshipStore, Relationship,
    render_relationship_block, LEVEL_CN,
)

# ---------- 1. 阈值边界 ----------
print("[1] 等级阈值")
cases = [
    (0, 0, False, "stranger"),
    (0, 1000, False, "stranger"),        # 只有旁观，没有直聊
    (1, 1, False, "stranger"),           # 首日
    (1, 23.9, False, "stranger"),        # 不到 24h
    (1, 24, False, "acquaintance"),      # 24h + 1 条直聊
    (1, 25, False, "acquaintance"),
    (299, 25, False, "acquaintance"),
    (299, 120, False, "acquaintance"),   # 差 1 条到了解
    (300, 120, False, "known"),
    (300, 119, False, "acquaintance"),   # 差 1 小时到了解
    (999, 360, False, "known"),          # 差 1 条到信任
    (1000, 360, False, "trust"),
    (1000, 359, False, "known"),         # 差 1 小时到信任
    (3000, 1080, False, "trust"),        # 未指定，最高信任
    (4000, 1200, False, "trust"),        # 高段非指定，不再出现空档
    (3000, 1080, True, "dependent"),
    (4000, 1200, True, "dependent"),
    (2999, 1080, True, "trust"),         # 条数差 1
    (3000, 1079, True, "trust"),         # 时间差 1 小时
    (1, 0, False, "stranger"),           # y=0
]
for msgs, hours, dep, expected in cases:
    got = compute_level(msgs, hours, dep, T)
    check("%d条/%dh/%s → %s" % (msgs, hours, "指定" if dep else "未指定",
                               LEVEL_CN[expected]), got == expected, got)

# ---------- 2. 傲娇/毒舌/玩梗开关 ----------
print("\n[2] 场景开关")
check("陌生：私聊禁止", tsundere_policy("stranger", False)[0] == "禁止")
check("认识：群聊禁止", tsundere_policy("acquaintance", True)[0] == "禁止")
check("了解：私聊禁止", tsundere_policy("known", False)[0] == "禁止")
check("信任：私聊允许", tsundere_policy("trust", False)[0] == "允许")
check("信任：群聊禁止", tsundere_policy("trust", True)[0] == "禁止")
check("依赖：私聊允许", tsundere_policy("dependent", False)[0] == "允许")
check("依赖：群聊允许", tsundere_policy("dependent", True)[0] == "允许")

# ---------- 3. 存储与持久化 ----------
print("\n[3] 存储与持久化")
tmp = Path(tempfile.mkdtemp(prefix="shelwell_rel_"))
store = RelationshipStore(db_path=str(tmp / "rel.sqlite3"),
                          dependent_id="1001", thresholds=T)
t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
store.observe("1001", "小明", "private", "1001", direct=True, ts=t0)
store.observe("1001", "小明", "private", "1001", direct=True,
              ts=t0 + timedelta(hours=1))
r = store.get("1001", now=t0 + timedelta(hours=122))
check("消息计数累加", r.msg_count == 2, r.msg_count)
check("直接互动计数累加", r.direct_count == 2, r.direct_count)
check("首次时间不被覆盖", r.first_seen.startswith("2026-01-01"), r.first_seen)
check("2 条直聊 + 122 小时 → 认识", r.level == "acquaintance", r.level)
check("显示名保存", r.display_name == "小明", r.display_name)

# 补到 300 条直聊：122 小时进入「了解」
for _ in range(298):
    store.observe("1001", "小明", "private", "1001", direct=True, ts=t0)
r = store.get("1001", now=t0 + timedelta(hours=122))
check("300 条直聊 + 122 小时 → 了解", r.direct_count == 300 and r.level == "known",
      f"{r.direct_count}/{r.level}")

store2 = RelationshipStore(db_path=str(tmp / "rel.sqlite3"),
                           dependent_id="1001", thresholds=T)
r2 = store2.get("1001", now=t0 + timedelta(hours=122))
check("重新打开后数据仍在", r2.direct_count == 300 and r2.display_name == "小明")
check("唯一依赖对象生效", store2.is_dependent("1001") and not store2.is_dependent("1002"))

# ---------- 4. 归档回填 ----------
print("\n[4] 归档回填")
from backend.memory.models import MemoryEvent
from backend.memory.archive import Archive

arc = Archive(history_dir=str(tmp / "history"))
for sid, name, n, ts in [("1001", "小明", 2, "2026-01-01T00:00:00+00:00"),
                         ("1002", "小红", 1, "2026-02-01T00:00:00+00:00")]:
    for _ in range(n):
        ev = MemoryEvent.create("qq:" + sid, "hi", "hello", speaker_id=sid,
                                channel_type="private", channel_id=sid,
                                speaker_name=name)
        ev.timestamp = ts
        arc.append(ev)

# 群聊旁观（没有 AI 回复）：只算 exposure，不算 direct
ev_group = MemoryEvent.create("qqg:9:1003", "群里闲聊", "", speaker_id="1003",
                              channel_type="group", channel_id="9",
                              speaker_name="小刚")
ev_group.timestamp = "2026-01-05T00:00:00+00:00"
arc.append(ev_group)

store3 = RelationshipStore(db_path=str(tmp / "rel_backfill.sqlite3"),
                           dependent_id="1001", thresholds=T)
n = store3.ensure_backfill(arc)
check("回填人数", n == 3, n)
rb = store3.get("1001", now=datetime(2026, 3, 1, tzinfo=timezone.utc))
check("回填条数", rb.msg_count == 2, rb.msg_count)
check("回填直接互动数", rb.direct_count == 2, rb.direct_count)
check("回填首次时间", rb.first_seen.startswith("2026-01-01"), rb.first_seen)
check("回填只跑一次", store3.ensure_backfill(arc) == 0)
check("回填后 2 条直聊 → 认识", rb.level == "acquaintance", rb.level)
rg = store3.get("1003", now=datetime(2026, 3, 1, tzinfo=timezone.utc))
check("群聊旁观不算直聊", rg.msg_count == 1 and rg.direct_count == 0,
      (rg.msg_count, rg.direct_count))
check("群聊旁观仍是陌生", rg.level == "stranger", rg.level)
store3.clear_all()
check("清空后不会被回填 resurrect", store3.ensure_backfill(arc) == 0)

# ---------- 5. 注入渲染 ----------
print("\n[5] 注入渲染")
unknown = Relationship(speaker_id="9", display_name="新朋友")
line = render_relationship_block(unknown, "private")
check("新人显示初次接触", "初次接触" in line and "等级：陌生" in line, line)
seen = Relationship(speaker_id="8", display_name="群里见过", msg_count=100,
                    direct_count=0, hours=50)
check("见过但没直聊", "还没直接聊过" in render_relationship_block(seen, "group"))
trusted = Relationship(speaker_id="1001", display_name="小明", msg_count=5000,
                       direct_count=1200, hours=1100, level="trust",
                       first_seen="2026-01-01T00:00:00+00:00")
check("信任私聊渲染允许", "允许" in render_relationship_block(trusted, "private"))
check("信任群聊渲染禁止", "禁止" in render_relationship_block(trusted, "group"))
dependent = Relationship(speaker_id="1001", display_name="小明", msg_count=6000,
                         direct_count=3200, hours=2200, level="dependent",
                         dependent=True)
check("依赖群聊渲染允许", "群聊也可" in render_relationship_block(dependent, "group"))

# ---------- 6. 上下文注入位置 ----------
print("\n[6] 上下文注入位置")
from backend.context_manager import ContextManager


class StubRelStore:
    def get(self, sid, display_name=""):
        return Relationship(speaker_id=sid, display_name=display_name or "小明",
                            msg_count=12, direct_count=12, hours=6,
                            level="stranger")


class StubMemory:
    relationship = StubRelStore()

    def search_semantic(self, *a, **kw):
        return []

    def search(self, *a, **kw):
        return []

    def get_group_context(self, *a, **kw):
        return []

    def get_stats(self):
        return {"total_memories": 0}


cm = ContextManager(memory_enabled=False)
cm.memory_enabled = True
cm.memory = StubMemory()
cm._knowledge_tried = True
cm._knowledge = None
out = cm.prepare_context([{"role": "user", "content": "在吗"}],
                         current_query="在吗", session_id="qq:1",
                         speaker_id="1", speaker_name="小明",
                         channel_type="private", system_as_role=True)
check("关系段不在 system 里", "【关系｜" not in out[0]["content"])
check("关系段在本轮 user 尾部", "【关系｜" in out[-1]["content"])
check("等级由代码写入", "等级：陌生" in out[-1]["content"])
check("原始消息保留", "在吗" in out[-1]["content"])

# ---------- 7. MemoryService 计数钩子 ----------
print("\n[7] MemoryService 计数钩子")
from backend.memory.service import MemoryService

svc = MemoryService(auto_worker=False, archive_dir=str(tmp / "h2"),
                    chroma_dir=str(tmp / "c2"), fts_db=str(tmp / "f2.sqlite3"),
                    jobs_db=str(tmp / "j2.sqlite3"))
# 避免测试机没开 Ollama 时触发嵌入请求
svc.indexer.index = lambda ev: {"vector": False, "lexical": True}
svc.add_memory("hello", "hi", session_id="qqg:9:1001", speaker_id="1001",
               channel_type="group", channel_id="9", speaker_name="小明",
               direct=True)
svc.add_memory("again", "hi", session_id="qqg:9:1001", speaker_id="1001",
               channel_type="group", channel_id="9", speaker_name="小明")
svc.add_memory("private hi", "hi", session_id="qq:2", speaker_id="2",
               channel_type="private", speaker_name="小红")
rel = svc.relationship.get("1001")
check("归档消息自动计数", rel.msg_count == 2, rel.msg_count)
check("直接互动单独计数", rel.direct_count == 1, rel.direct_count)
check("私聊默认算直接互动", svc.relationship.get("2").direct_count == 1)
check("关系存储与 MemoryService 同源", svc.relationship.db_path.endswith("relationships.sqlite3"))
svc.stop_worker()

# ---------- 8. 提示词门槛（防止回退到无条件傲娇） ----------
print("\n[8] 提示词门槛")
from backend.persona_engine import PersonaEngine
persona = PersonaEngine().prompt
check("人格包含五级社交分寸", "【社交分寸" in persona and "依赖：" in persona, persona[:80])
check("人格写死群聊门槛", "群聊只有“依赖”解锁" in persona)
check("输出规范写死禁止档", "开关为“禁止”时" in cm.OUTPUT_FORMAT)
check("输出规范禁止复述等级", "不得把等级、条数、时长说出" in cm.OUTPUT_FORMAT)
check("旧的无条件傲娇未残留", "可以吐槽，可以怼；但对方情绪低落" not in cm.OUTPUT_FORMAT)

shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
