# scripts/test_knowledge.py
# 职责：知识库回归测试——入库/检索/阈值/与记忆库分离/注入标签
# 用法：.venv\Scripts\python.exe scripts\test_knowledge.py

import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.knowledge_base import KnowledgeBase

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


class FakeEmbedder:
    """按关键词返回固定向量，避免测试依赖 Ollama"""
    def embed_safe(self, text):
        t = text or ""
        if "机动球" in t:
            return [1.0, 0.0, 0.0, 0.0]
        if "酒吧" in t:
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 1.0, 0.0]


tmp = Path(tempfile.mkdtemp(prefix="shelwell_kb_"))
kb = KnowledgeBase(chroma_dir=str(tmp / "chroma"), collection_name="kb_test",
                   embedder=FakeEmbedder())

items = [
    {"id": "K1", "title": "机动球死亡率", "category": "society", "canon": "原文",
     "aliases": ["机动球"], "questions": ["机动球危险吗"],
     "content": "机动球死亡率七成。", "embed_text": "机动球死亡率 机动球 机动球危险吗 七成"},
    {"id": "K2", "title": "猎人酒吧", "category": "place", "canon": "原文",
     "aliases": ["酒吧"], "questions": ["酒吧在哪"],
     "content": "猎人酒吧是赏金猎人据点。", "embed_text": "猎人酒吧 酒吧 酒吧在哪 据点"},
]
print("[1] 入库")
r = kb.ingest(items, rebuild=True)
check("全部入库", r["ok"] == 2, r)
check("集合条数", kb.stats()["count"] == 2, kb.stats())

print("\n[2] 检索")
hits = kb.search("机动球死亡率", top_k=2, min_similarity=0.55)
check("命中正确条目", hits and hits[0]["id"] == "K1", hits)
check("正文可取", "七成" in (hits[0]["content"] if hits else ""), hits)
check("阈值过滤无关查询", kb.search("我今天心情不好", min_similarity=0.55) == [])

print("\n[3] 幂等与重建")
kb.ingest(items)
check("重复入库不翻倍", kb.stats()["count"] == 2, kb.stats())
kb.ingest(items[:1], rebuild=True)
check("重建后只剩 1 条", kb.stats()["count"] == 1, kb.stats())

print("\n[4] 与记忆库分离（独立 collection）")
import chromadb
client = chromadb.PersistentClient(path=str(tmp / "chroma"))
names = sorted(c.name for c in client.list_collections())
check("知识集合独立存在", names == ["kb_test"], names)

print("\n[5] 注入标签（不混入 system）")
from backend.context_manager import ContextManager


class FakeKB:
    def search(self, query, top_k=2, min_similarity=0.55):
        return [{"id": "K1", "title": "机动球死亡率",
                 "content": "机动球死亡率七成。", "similarity": 0.9,
                 "category": "society"}]


cm = ContextManager(memory_enabled=False)
cm._knowledge_tried = True
cm._knowledge = FakeKB()
out = cm.prepare_context([{"role": "user", "content": "机动球危险吗"}],
                         current_query="机动球危险吗", session_id="t",
                         system_as_role=True)
sys_txt = out[0]["content"]
user_txt = out[-1]["content"]
check("标签在 user 尾部", "【你世界里的档案" in user_txt, user_txt[:80])
check("标签不在 system", "【你世界里的档案" not in sys_txt)
check("档案正文被注入", "七成" in user_txt)
check("明确声明不是对话记忆", "不是对话记忆" in user_txt)

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
