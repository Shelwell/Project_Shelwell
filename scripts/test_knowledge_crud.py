# scripts/test_knowledge_crud.py
# 知识库 UI 后端：JSONL CRUD、列表、删除、导入、重建。
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
    def embed_safe(self, text):
        t = text or ""
        if "萨勒姆" in t:
            return [1.0, 0.0, 0.0]
        if "酒吧" in t:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


tmp = Path(tempfile.mkdtemp(prefix="shelwell_kb_crud_"))
jsonl = tmp / "knowledge.jsonl"
kb = KnowledgeBase(chroma_dir=str(tmp / "chroma"), collection_name="kb_crud",
                   embedder=FakeEmbedder(), jsonl_path=str(jsonl))

print("[1] 新建")
r = kb.upsert_item({
    "id": "K001", "title": "萨勒姆", "category": "place",
    "aliases": "萨勒姆, Zalem", "questions": "萨勒姆是什么？",
    "content": "萨勒姆是天空城。",
})
check("created", r.get("created") is True, r)
check("embed_text 自动生成", "天空城" in r["item"]["embed_text"], r["item"])
check("JSONL 落盘", jsonl.exists() and "K001" in jsonl.read_text(encoding="utf-8"))
check("Chroma 有一条", kb.stats()["count"] == 1, kb.stats())

print("[2] 列表/搜索")
res = kb.list_items()
check("列表一条", res["total"] == 1, res)
check("分类列表", "place" in res["categories"], res)
check("关键词命中", kb.list_items(keyword="天空城")["total"] == 1, res)
check("分类过滤", kb.list_items(category="place")["total"] == 1, res)
check("无关关键词", kb.list_items(keyword="不存在")["total"] == 0)

print("[3] 更新")
r2 = kb.upsert_item({
    "id": "K001", "title": "萨勒姆", "category": "place",
    "aliases": ["萨勒姆"], "content": "萨勒姆是万米高空的天空城。",
})
check("updated", r2.get("created") is False, r2)
check("正文更新", "万米" in kb.get_item("K001")["content"])
check("Chroma 不翻倍", kb.stats()["count"] == 1, kb.stats())

print("[4] 自动 ID")
check("next_id", kb.next_id() == "K002", kb.next_id())

print("[5] 删除")
rd = kb.delete_item("K001")
check("delete ok", rd.get("ok") is True, rd)
check("JSONL 已删", kb.list_items()["total"] == 0)
check("Chroma 已删", kb.stats()["count"] == 0, kb.stats())

print("[6] 导入 + 重建")
src = tmp / "import.jsonl"
src.write_text(
    '{"id":"K010","title":"猎人酒吧","category":"place","content":"酒吧是据点。"}\n'
    '{"id":"K011","title":"垃圾山","category":"place","content":"垃圾山能捡零件。"}\n',
    encoding="utf-8")
ri = kb.import_jsonl(str(src))
check("imported", ri.get("ok") and ri.get("total") == 2, ri)
check("import list", kb.list_items()["total"] == 2, kb.list_items())
check("Chroma 两条", kb.stats()["count"] == 2, kb.stats())
rb = kb.rebuild_index()
check("rebuild ok", rb.get("ok") == 2, rb)
check("rebuild 后仍两条", kb.stats()["count"] == 2, kb.stats())

print("[7] 原子写文件存在")
check("no tmp left", not (tmp / "knowledge.jsonl.tmp").exists())

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
