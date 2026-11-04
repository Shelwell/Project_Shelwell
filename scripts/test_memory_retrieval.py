# scripts/test_memory_retrieval.py
# 职责：记忆检索回归测试——多词查询召回、集合自愈
# 用法：.venv\Scripts\python.exe scripts/test_memory_retrieval.py

import sys
import tempfile
import shutil
import chromadb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.memory.indexer import Indexer, seg_query
from backend.memory.service import MemoryService

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


print("[1] 查询分词：多词必须用 OR")
q = seg_query("我喜欢什么游戏")
check("包含显式 OR", " OR " in q, q)
check("每个词仍加引号", q.count('"') >= 4, q)
check("空查询安全", seg_query("") == "")

tmp = Path(tempfile.mkdtemp(prefix="shelwell_retr_"))
svc = MemoryService(auto_worker=False, archive_dir=str(tmp / "h"),
                    chroma_dir=str(tmp / "c"), fts_db=str(tmp / "f.sqlite3"),
                    jobs_db=str(tmp / "j.sqlite3"))
# 关掉向量路径，单独验证关键词召回（避免依赖 Ollama 嵌入模型）
svc.indexer.embedder.embed_safe = lambda text: None

svc.add_memory("我最近在玩明日方舟，这个塔防游戏挺上头", "",
               session_id="qq:1001", speaker_id="1001", channel_type="private",
               channel_id="1001", speaker_name="小明")
svc.add_memory("今天加班到十点，累死了", "",
               session_id="qq:1001", speaker_id="1001", channel_type="private",
               channel_id="1001", speaker_name="小明")

print("\n[2] 多词查询能召回（旧实现是 AND，必然 0 命中）")
hits = svc.search("你还记得我喜欢玩什么游戏吗", top_k=3,
                  speaker_id="1001", channel_type="private",
                  session_id="qq:1001")
check("有召回", len(hits) >= 1, len(hits))
check("命中游戏那条", any("明日方舟" in h["content"] for h in hits),
      [h["content"][:30] for h in hits])
check("带 user_msg 元数据", all("user_msg" in h["metadata"] for h in hits))

print("\n[3] 作用域隔离")
hits_other = svc.search("游戏", top_k=3, speaker_id="2002",
                        channel_type="private", session_id="qq:2002")
check("别人查不到他的记忆", len(hits_other) == 0, len(hits_other))

print("\n[4] 集合被外部删除后自动重建")
ch = chromadb.PersistentClient(path=str(tmp / "c"))
for col in ch.list_collections():
    ch.delete_collection(col.name)
try:
    svc.indexer._ensure_collection()
    svc.indexer.index(type("E", (), {
        "id": "epi_x", "combined_text": "测试", "user_msg": "测试", "ai_msg": "",
        "timestamp": "2026-11-04T00:00:00", "session_id": "s", "speaker_id": "1",
        "channel_type": "private", "channel_id": "1", "score": 0.0,
    })())
    ok = True
    err = ""
except Exception as e:
    ok, err = False, str(e)
check("删除后重建句柄不报错", ok, err)
check("重建后集合可用", svc.indexer.collection.count() >= 0)

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
