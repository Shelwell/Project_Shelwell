# scripts/test_memory_edit.py
# 职责：验证情景记忆修改（未提炼可改，已提炼只读）与归档/索引重建
# 用法：.venv\Scripts\python.exe scripts\test_memory_edit.py

import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.memory.service import MemoryService

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp(prefix="shelwell_mem_edit_"))
svc = MemoryService(auto_worker=False, archive_dir=str(tmp / "h"),
                    chroma_dir=str(tmp / "c"), fts_db=str(tmp / "f.sqlite3"),
                    jobs_db=str(tmp / "j.sqlite3"))
# 测试机不一定开着 Ollama：向量失败时仍会写 FTS，这里只记录调用
indexed = []
svc.indexer.index = lambda ev: indexed.append((ev.id, ev.user_msg, ev.ai_msg)) \
    or {"vector": False, "lexical": True}

svc.add_memory("旧用户消息", "旧回复", session_id="qqg:9:1001",
               speaker_id="1001", channel_type="group", channel_id="9",
               speaker_name="小明")
ev = svc.archive.list_by_session("qqg:9:1001", limit=10)[0]
eid = ev.id

print("[1] 未提炼：可修改")
r = svc.update_event(eid, "新用户消息", "新回复")
check("返回成功", r.get("ok") is True, r)
updated = svc.archive.get(eid)
check("用户消息已改", updated.user_msg == "新用户消息", updated.user_msg)
check("AI 回复已改", updated.ai_msg == "新回复", updated.ai_msg)
check("ID 不变", updated.id == eid)
check("时间不变", updated.timestamp == ev.timestamp)
check("索引已重建调用", indexed and indexed[-1][1:] == ("新用户消息", "新回复"), indexed)

print("\n[2] 已提炼：只能删除")
updated.consolidated = True
svc.archive.update(updated)
r = svc.update_event(eid, "再改一次", "再改一次")
check("拒绝修改", r.get("ok") is False, r)
check("拒绝原因明确", "只能删除" in r.get("message", ""), r.get("message"))
after = svc.archive.get(eid)
check("内容未被改动", after.user_msg == "新用户消息" and after.ai_msg == "新回复",
      (after.user_msg, after.ai_msg))

print("\n[3] 参数校验")
check("两个都为空被拒绝", svc.update_event(eid, "", "").get("ok") is False)
check("不存在的 ID 被拒绝", svc.update_event("epi_nope", "x", "y").get("ok") is False)

print("\n[4] 归档层直接更新保留字段")
ev2 = svc.archive.get(eid)
ev2.consolidated = False
ev2.emotion = "calm"
svc.archive.update(ev2)
ev3 = svc.archive.get(eid)
check("额外字段保留", ev3.emotion == "calm", ev3.emotion)
check("consolidated 未被误改", ev3.consolidated is False, ev3.consolidated)

svc.stop_worker()
shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
