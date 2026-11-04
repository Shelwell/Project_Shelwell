# scripts/build_knowledge_jsonl.py
# 职责：把人写的分块知识库（Markdown）转成向量库入库用的 JSONL，并做质量校验
# 用法：.venv\Scripts\python.exe scripts\build_knowledge_jsonl.py
# 校验项：必填字段、正文长度（50-220 字）、别名/问法是否齐全、related 引用是否存在

import sys
import re
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

SRC = ROOT / "config" / "persona_prompts" / "shelwell_knowledge.md"
OUT = ROOT / "config" / "persona_prompts" / "shelwell_knowledge.jsonl"

MIN_LEN, MAX_LEN = 60, 180
EMBED_MIN, EMBED_MAX = 100, 320
REQUIRED = ("category", "aliases", "questions", "content")


def parse_md(text: str) -> list:
    """按 ### 标题切块，解析每个块的 - key: value 字段"""
    blocks = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"^###\s+(\S+)\s+(.+?)\s*$", line)
        if m:
            if cur:
                blocks.append(cur)
            cur = {"id": m.group(1), "title": m.group(2), "_fields": {}}
            continue
        if cur is None:
            continue
        fm = re.match(r"^-\s*([a-z_]+):\s*(.*)$", line.strip())
        if fm:
            cur["_fields"][fm.group(1)] = fm.group(2).strip()
    if cur:
        blocks.append(cur)

    out = []
    for b in blocks:
        f = b.pop("_fields")
        item = {
            "id": b["id"],
            "title": b["title"],
            "category": f.get("category", ""),
            "aliases": [x.strip() for x in f.get("aliases", "").split(",") if x.strip()],
            "questions": [x.strip() for x in f.get("questions", "").split("/") if x.strip()],
            "canon": f.get("canon", ""),
            "content": f.get("content", ""),
            "related": [x.strip() for x in f.get("related", "").split(",") if x.strip()],
            "source": "config/persona_prompts/shelwell_knowledge.md",
        }
        # 检索用文本：标题 + 别名 + 问法 + 正文（问法让口语提问也能命中）
        item["embed_text"] = " ".join(
            [item["title"]] + item["aliases"] + item["questions"] + [item["content"]]
        )
        out.append(item)
    return out


def main():
    if not SRC.exists():
        print(f"找不到知识源: {SRC}")
        return 1
    items = parse_md(SRC.read_text(encoding="utf-8"))
    ids = {i["id"] for i in items}
    problems = []
    lengths = []

    for it in items:
        n = len(it["content"])
        lengths.append(n)
        for k in REQUIRED:
            if not it.get(k):
                problems.append(f"{it['id']} 缺字段 {k}")
        if not (MIN_LEN <= n <= MAX_LEN):
            problems.append(f"{it['id']} 正文 {n} 字，超出 {MIN_LEN}-{MAX_LEN}")
        en = len(it["embed_text"])
        if not (EMBED_MIN <= en <= EMBED_MAX):
            problems.append(f"{it['id']} embed_text {en} 字，超出 {EMBED_MIN}-{EMBED_MAX}")
        if len(it["aliases"]) < 2:
            problems.append(f"{it['id']} 别名少于 2 个")
        if len(it["questions"]) < 1:
            problems.append(f"{it['id']} 缺少检索问法")
        for r in it["related"]:
            if r not in ids:
                problems.append(f"{it['id']} related 引用了不存在的 {r}")

    OUT.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n",
                   encoding="utf-8")

    from collections import Counter
    cats = Counter(i["category"] for i in items)
    print(f"分块数: {len(items)}  →  {OUT.relative_to(ROOT)}")
    print(f"正文长度: 最短 {min(lengths)} / 平均 {sum(lengths)//len(lengths)} / 最长 {max(lengths)}")
    print("分类分布:", dict(cats))
    print(f"别名总数: {sum(len(i['aliases']) for i in items)} | "
          f"检索问法总数: {sum(len(i['questions']) for i in items)}")
    if problems:
        print(f"\n校验问题 {len(problems)} 条:")
        for p in problems:
            print("  -", p)
        return 1
    print("\n校验通过，可用于入库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
