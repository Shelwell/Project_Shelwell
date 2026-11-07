# scripts/migrate_people_labels.py
# 把已有语义事实和情景归档中的昵称统一成 PeopleRegistry 的 [YY]。
#
# 默认 dry-run，只打印会改什么：
#   .venv\Scripts\python.exe scripts/migrate_people_labels.py
# 实际写入：
#   .venv\Scripts\python.exe scripts\migrate_people_labels.py --apply

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.people import PeopleRegistry
from backend.memory.semantic import SemanticStore
from backend.memory.archive import Archive


def normalize_subject(people, raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = people.scrub(text)
    text = people.scrub_self(text)
    if text.startswith("["):
        return text
    mapped = people.label_for_name(text)
    return mapped or ("[" + text + "]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入；默认只预览")
    args = ap.parse_args()
    people = PeopleRegistry()

    print("people:", len(people.people), "|", people.path)
    sem = SemanticStore()

    # 1. 语义事实
    all_facts = []
    page = 1
    while True:
        r = sem.list_facts(page=page, page_size=200)
        all_facts.extend(r.get("items") or [])
        if page >= int(r.get("pages", 1) or 1):
            break
        page += 1

    changed = 0
    print("\n[语义事实]", len(all_facts), "条")
    for f in all_facts:
        subject = normalize_subject(people, f.subject)
        obj = people.scrub_self(people.scrub(str(f.object or "")))
        fact = people.scrub_self(people.scrub(str(f.fact or "")))
        patch = {}
        if subject and subject != f.subject:
            patch["subject"] = subject
        if obj != (f.object or ""):
            patch["object"] = obj
        if fact != (f.fact or ""):
            patch["fact"] = fact
        if not patch:
            continue
        changed += 1
        print(f"[语义] {f.id} | {f.fact[:50]}")
        print("      ->", fact[:80], "| subject", subject)
        if args.apply:
            if not sem.update(f.id, patch):
                print("      写入失败")

    # 2. 情景归档：只补 speaker_label，不改原文
    arc = Archive()
    by_day = defaultdict(list)
    for ev in arc._iter_all():
        by_day[(ev.timestamp or "")[:10]].append(ev)

    epi_changed = 0
    print("\n[情景归档]", sum(len(v) for v in by_day.values()), "条")
    for day, events in sorted(by_day.items()):
        path = Path(arc.history_dir) / f"{day}.jsonl"
        if not path.exists():
            continue
        lines = []
        local_changed = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                lines.append(line)
                continue
            label = d.get("speaker_label") or people.label(
                d.get("speaker_id", ""), fallback=d.get("speaker_name", ""))
            if label and d.get("speaker_label") != label:
                d["speaker_label"] = label
                local_changed += 1
            lines.append(json.dumps(d, ensure_ascii=False))
        if local_changed:
            epi_changed += local_changed
            print(f"[情景] {day}: {local_changed} 条补 speaker_label")
            if args.apply:
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(path)

    print("\n预览：语义", changed, "条；情景", epi_changed, "条")
    print("已写入" if args.apply else "未写入（加 --apply 执行）")


if __name__ == "__main__":
    main()
