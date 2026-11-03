# scripts/test_extractor_identity.py
# 职责：回归测试——语义提炼必须按发言人区分，主语不得写成“用户”
# 用法：.venv\Scripts\python.exe scripts\test_extractor_identity.py

import sys
import json
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.memory.models import MemoryEvent
from backend.memory.extractor import Extractor
from backend.memory.archive import Archive
from backend.memory.consolidator import Consolidator
from backend.memory.semantic import SemanticStore

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


def ev(speaker_id, speaker_name, text, channel="private", channel_id="1"):
    e = MemoryEvent.create(f"qq:{speaker_id}", text, "", speaker_id=speaker_id,
                           channel_type=channel, channel_id=channel_id,
                           speaker_name=speaker_name)
    return e


# ---------- 1. 提示词：带上发言人 ----------
print("[1] 提炼提示词")
ex = Extractor(provider_call=lambda m: '{"facts": []}')
msgs, who = ex.build_messages([ev("1001", "小明", "我最近在学做菜")],
                              speaker_name="小明", owner_id="1001")
sys_text = msgs[0]["content"]
user_text = msgs[1]["content"]
check("who 取昵称", who == "小明", who)
check("提示词含发言人", "小明" in sys_text and "1001" in sys_text)
check("明确禁止“用户”", "禁止使用「用户」" in sys_text)
check("对话行带发言人前缀", user_text.startswith("小明："), user_text[:20])
check("不再出现“关于「用户」”", "关于「用户」" not in sys_text)

# 无昵称时退回 ID
_, who2 = ex.build_messages([ev("1001", "", "x")], speaker_name="", owner_id="1001")
check("无昵称退回 ID", who2 == "1001", who2)

# ---------- 2. 解析：模糊主语被纠正 ----------
print("\n[2] 解析归一化")
raw = json.dumps({"facts": [
    {"subject": "用户", "predicate": "喜欢", "object": "冰美式",
     "fact": "用户喜欢喝冰美式", "tag": "preference"},
    {"subject": "小明", "predicate": "在学", "object": "做菜",
     "fact": "小明在学做菜", "tag": "misc"},
]}, ensure_ascii=False)
facts = ex._parse(raw, "小明")
check("模糊主语被替换", facts[0]["subject"] == "小明", facts[0]["subject"])
check("fact 开头被替换", facts[0]["fact"] == "小明喜欢喝冰美式", facts[0]["fact"])
check("正常主语保留", facts[1]["subject"] == "小明")

# ---------- 3. 巩固：多人事实分开归属 ----------
print("\n[3] 巩固与归属")
tmp = Path(tempfile.mkdtemp(prefix="shelwell_extract_"))
arc = Archive(history_dir=str(tmp / "history"))
store = SemanticStore(db_path=str(tmp / "sem.sqlite3"), chroma_dir=str(tmp / "chroma"))

# 两个发言人，各自两条消息
for e in [
    ev("1001", "小明", "我喜欢喝冰美式"),
    ev("1001", "小明", "我下周去成都"),
    ev("2002", "小红", "我喜欢打羽毛球"),
]:
    arc.append(e)

captured = {}


class FakeExtractor:
    """按传入的发言人身份返回一条事实，并记录调用参数"""
    def extract(self, events, speaker_name="", owner_id=""):
        captured.setdefault(owner_id, []).append(speaker_name)
        return [{
            "subject": speaker_name, "predicate": "喜欢", "object": "x",
            "fact": f"{speaker_name}喜欢x", "tag": "preference",
            "source_ids": [e.id for e in events],
        }]


c = Consolidator(archive=arc, store=store, extractor=FakeExtractor())
r = c.consolidate(limit=50)
check("处理 3 条情景", r["processed"] == 3, r)

items = store.list_facts(page_size=20)["items"]
subjects = sorted({f.subject for f in items})
check("主语是两个人", subjects == ["小明", "小红"], subjects)
check("没有“用户”主语", "用户" not in subjects, subjects)

import sqlite3
con = sqlite3.connect(str(tmp / "sem.sqlite3"))
owners = dict(con.execute("SELECT subject, owner_id FROM facts").fetchall())
con.close()
check("事实归属正确", owners.get("小明") == "1001" and owners.get("小红") == "2002", owners)
check("提炼时传入了昵称",
      captured.get("1001") == ["小明"] and captured.get("2002") == ["小红"], captured)

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
