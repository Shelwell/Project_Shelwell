# scripts/ingest_knowledge.py
# 职责：把 shelwell_knowledge.jsonl 灌入 ChromaDB 的 shelwell_knowledge 集合
# 用法：
#   .venv\Scripts\python.exe scripts/ingest_knowledge.py            # 增量/覆盖（按 id 幂等）
#   .venv\Scripts\python.exe scripts/ingest_knowledge.py --rebuild  # 清空重建
#   .venv\Scripts\python.exe scripts/ingest_knowledge.py --stats    # 只看现状

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.config_loader import get
from backend.knowledge_base import build_from_config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None, help="JSONL 路径，默认取 app_config.knowledge.path")
    ap.add_argument("--rebuild", action="store_true", help="清空集合后重建")
    ap.add_argument("--stats", action="store_true", help="只打印现状")
    a = ap.parse_args()

    cfg = get("knowledge", {}) or {}
    path = a.path or cfg.get("path") or "config/persona_prompts/shelwell_knowledge.jsonl"
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path

    kb = build_from_config(str(ROOT))
    print(f"集合: {kb.collection_name} | 当前条数: {kb.stats().get('count')}")

    if a.stats:
        return 0
    if not path.exists():
        print(f"找不到知识源: {path}")
        return 1

    print(f"知识源: {path.relative_to(ROOT)}")
    r = kb.ingest_jsonl(str(path), rebuild=a.rebuild)
    print(f"入库完成: 总 {r['total']} | 成功 {r['ok']} | 跳过 {r['skipped']}"
          + ("（已重建）" if r["rebuild"] else ""))
    print(f"集合现状: {kb.stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
