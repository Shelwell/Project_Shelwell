# backend/memory/consolidator.py
# 职责：巩固流程——情景记忆 → 语义事实
# 流程：选源 → 提炼 → 去重比对 → 新增/更新 → 标记已提炼

from backend.memory.archive import Archive
from backend.memory.extractor import Extractor, EXTRACTOR_VERSION
from backend.memory.models import SemanticFact, now_iso
from backend.memory.semantic import SemanticStore


class Consolidator:
    """情景 → 语义的巩固器"""

    def __init__(self, archive: Archive = None, store: SemanticStore = None,
                 extractor: Extractor = None):
        self.archive = archive or Archive()
        self.store = store or SemanticStore()
        self.extractor = extractor or Extractor()

    def consolidate(self, session_filter: str = None, limit: int = 50,
                    dry_run: bool = False) -> dict:
        """
        执行一轮巩固。
        session_filter: 只处理某个会话（None 表示全部）
        limit: 单次最多处理多少条情景记忆
        dry_run: 只提炼不写库（预览用）
        返回 {"processed": n, "facts_new": n, "facts_merged": n, "facts": [...]}
        """
        events = [e for e in self.archive.list_unconsolidated(limit=limit)
                  if not session_filter or e.session_id == session_filter]
        if not events:
            return {"processed": 0, "facts_new": 0, "facts_merged": 0, "facts": []}

        # 按 owner（speaker_id）分组，不同人的事实不混
        groups = {}
        for ev in events:
            key = ev.speaker_id or ev.session_id
            groups.setdefault(key, []).append(ev)

        total_new = 0
        total_merged = 0
        all_facts = []

        failed = 0
        for owner_id, evs in groups.items():
            # 提取失败时抛异常，此时不标记已提炼，下轮可重试
            try:
                facts = self.extractor.extract(evs)
            except Exception as e:
                failed += 1
                print(f"[Consolidator] owner={owner_id} 提炼失败，跳过标记: {e}")
                continue

            for f in facts:
                action = self._upsert_fact(f, owner_id, dry_run=dry_run)
                all_facts.append(action)
                if action["action"] == "new":
                    total_new += 1
                elif action["action"] == "merged":
                    total_merged += 1

            # 只有提取成功（含正常返回空）才标记已提炼
            if not dry_run:
                for ev in evs:
                    ev.consolidated = True
                    self._mark_consolidated(ev.id)

        return {
            "processed": len(events),
            "facts_new": total_new,
            "facts_merged": total_merged,
            "failed": failed,
            "facts": all_facts,
        }

    def _upsert_fact(self, f: dict, owner_id: str, dry_run: bool = False) -> dict:
        """单条事实去重比对后新增或合并"""
        # 1. 强匹配：同 owner + 同 subject/predicate/object
        existing = self._find_strong_match(f, owner_id)
        if existing:
            if not dry_run:
                merged_src = list(set(existing.source_ids + f["source_ids"]))
                self.store.update(existing.id, {
                    "source_ids": merged_src,
                    "confidence": min(1.0, existing.confidence + 0.15),
                    "strength": min(2.0, existing.strength + 0.3),
                    "hits": (existing.hits or 0) + 1,
                    "last_hit": now_iso(),
                })
            return {"action": "merged", "fact": existing.fact, "id": existing.id}

        # 2. 弱匹配：语义相似度高的已有事实
        hits = self.store.search(f["fact"], top_k=1, min_similarity=0.85, owner_id=owner_id)
        if hits:
            existing = hits[0]["fact"]
            if not dry_run:
                merged_src = list(set(existing.source_ids + f["source_ids"]))
                self.store.update(existing.id, {
                    "source_ids": merged_src,
                    "confidence": min(1.0, existing.confidence + 0.1),
                    "strength": min(2.0, existing.strength + 0.2),
                    "hits": (existing.hits or 0) + 1,
                    "last_hit": now_iso(),
                })
            return {"action": "merged", "fact": existing.fact, "id": existing.id}

        # 3. 新增
        fact_obj = SemanticFact.create(
            fact=f["fact"], source_ids=f["source_ids"],
            subject=f.get("subject", ""), predicate=f.get("predicate", ""),
            object=f.get("object", ""), tag=f.get("tag", "misc"),
        )
        fact_obj.extractor_version = EXTRACTOR_VERSION
        if not dry_run:
            self.store.add(fact_obj, owner_id=owner_id, owner_type="user")
        return {"action": "new", "fact": f["fact"], "id": fact_obj.id}

    def _find_strong_match(self, f: dict, owner_id: str):
        """同 owner 下三元组完全一致的事实"""
        with self.store._connect() as con:
            row = con.execute(
                "SELECT * FROM facts WHERE owner_id=? AND subject=? AND predicate=? AND object=? AND state='active' LIMIT 1",
                (owner_id, f.get("subject", ""), f.get("predicate", ""), f.get("object", "")),
            ).fetchone()
        return self.store._row_to_fact(row) if row else None

    def _mark_consolidated(self, event_id: str):
        """标记情景记忆已提炼（更新 JSONL 里的 consolidated 字段）"""
        import json
        import os
        ev = self.archive.get(event_id)
        if not ev:
            return
        day = ev.timestamp[:10]
        path = os.path.join(self.archive.history_dir, f"{day}.jsonl")
        if not os.path.exists(path):
            return
        lines = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    lines.append(line)
                    continue
                if d.get("id") == event_id:
                    d["consolidated"] = True
                lines.append(json.dumps(d, ensure_ascii=False))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)