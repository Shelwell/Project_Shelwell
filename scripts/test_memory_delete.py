# scripts/test_memory_delete.py
# 职责：记忆删除功能回归测试——全部在临时目录进行，不碰真实记忆库
# 用法：.venv\Scripts\python.exe scripts\test_memory_delete.py

import sys
import tempfile
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.memory.models import MemoryEvent, SemanticFact
from backend.memory.archive import Archive
from backend.memory.indexer import Indexer
from backend.memory.semantic import SemanticStore

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp(prefix="shelwell_memtest_"))
print(f"临时目录: {tmp}")

# ---------- 1. 语义事实：单条删除 / 来源剥离 / 清空 ----------
print("\n[1] 语义事实")
sem = SemanticStore(db_path=str(tmp / "semantic.sqlite3"),
                    chroma_dir=str(tmp / "chroma"))
f1 = SemanticFact(id="sem_a", fact="用户喜欢冰美式", source_ids=["epi_a"])
f2 = SemanticFact(id="sem_b", fact="用户周三去爬山", source_ids=["epi_a", "epi_b"])
f3 = SemanticFact(id="sem_c", fact="用户养了一只猫", source_ids=["epi_c"])
for f in (f1, f2, f3):
    sem.add(f)
check("写入 3 条", sem.stats()["total"] == 3, sem.stats())

check("单条删除返回 True", sem.delete("sem_a") is True)
check("删除后剩 2 条", sem.stats()["total"] == 2, sem.stats())

n = sem.detach_source("epi_a")
check("剥离来源命中 1 条", n == 1, n)
check("仅剩唯一来源的事实被级联删除", sem.stats()["total"] == 2, sem.stats())
check("多来源事实被改写",
      sem.get("sem_b").source_ids == ["epi_b"], sem.get("sem_b").source_ids)

# 再删掉最后一个来源 → sem_b 应被级联删除
n2 = sem.detach_source("epi_b")
check("最后一个来源剥离", n2 == 1, n2)
check("无来源事实被级联删除", sem.stats()["total"] == 1, sem.stats())
check("存活的是 sem_c", sem.get("sem_c") is not None and sem.get("sem_b") is None)

check("清空返回条数", sem.clear_all() == 1)
check("清空后为 0", sem.stats()["total"] == 0, sem.stats())

# ---------- 2. 情景归档：单条删除 ----------
print("\n[2] 情景归档")
arc = Archive(history_dir=str(tmp / "history"))
evs = [MemoryEvent.create("qq:1", f"消息{i}", f"回复{i}", channel_id="1")
       for i in range(3)]
for e in evs:
    arc.append(e)
check("归档 3 条", arc.stats()["total"] == 3, arc.stats())
check("删除单条", arc.delete(evs[1].id) is True)
check("删除后剩 2 条", arc.stats()["total"] == 2, arc.stats())
check("删的是目标 ID", arc.get(evs[1].id) is None)
check("其他记录仍在", arc.get(evs[0].id) is not None)

# ---------- 3. 索引：FTS 删除 ----------
print("\n[3] 索引")
idx = Indexer(chroma_dir=str(tmp / "chroma2"), fts_db=str(tmp / "fts.sqlite3"))
for e in evs[:2]:
    idx.index(e)
check("FTS 写入 2 条", idx.stats()["lexical"] == 2, idx.stats())
idx.delete(evs[0].id)
check("FTS 删除后剩 1 条", idx.stats()["lexical"] == 1, idx.stats())

# ---------- 4. 关键回归：另一个进程持有句柄时，清表仍可执行 ----------
print("\n[4] 并发锁（WinError 32 回归）")
holder = sqlite3.connect(str(tmp / "fts.sqlite3"), timeout=10)
holder.execute("SELECT COUNT(*) FROM fts_episodic").fetchone()   # 持有一个读连接
try:
    con = sqlite3.connect(str(tmp / "fts.sqlite3"), timeout=10)
    con.execute("DELETE FROM fts_episodic")
    con.commit()
    con.close()
    ok = True
    err = ""
except Exception as e:
    ok = False
    err = str(e)
holder.close()
check("他进程持句柄时 DELETE 可用", ok, err)
check("清表后为 0", idx.stats()["lexical"] == 0, idx.stats())

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
import shutil
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if FAIL else 0)
