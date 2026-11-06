# scripts/rebuild_index.py
# 职责：从 JSONL 归档重建情景记忆索引（向量 + FTS），用于索引丢失/不一致时修复
# 用法：.venv\Scripts\python.exe scripts\rebuild_index.py [--vector-only|--lexical-only]
# 说明：向量重建需要 Ollama 的嵌入模型可用；FTS 只依赖本地 sqlite

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.memory.archive import Archive
from backend.memory.indexer import Indexer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条（0=全部）")
    a = ap.parse_args()

    arc = Archive()
    idx = Indexer()
    events = list(arc._iter_all())
    if a.limit:
        events = events[:a.limit]

    print(f"归档记录: {len(events)} 条")
    ok_v = ok_l = fail = 0
    for i, ev in enumerate(events, 1):
        try:
            r = idx.index(ev)
            ok_v += 1 if r.get("vector") else 0
            ok_l += 1 if r.get("lexical") else 0
        except Exception as e:
            fail += 1
            print(f"  [{i}] {ev.id} 失败: {e}")
        if i % 20 == 0:
            print(f"  ... {i}/{len(events)}")

    st = idx.stats()
    print(f"\n完成: 向量 {ok_v} | 关键词 {ok_l} | 失败 {fail}")
    print(f"索引现状: 向量 {st['vector']} 条 | 关键词 {st['lexical']} 条")
    if st["vector"] < st["lexical"]:
        print("提示：向量数少于关键词数，通常是嵌入模型不可用（检查 Ollama）")


if __name__ == "__main__":
    main()
