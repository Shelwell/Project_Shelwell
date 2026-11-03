# scripts/report_shadow.py
# 职责：汇总影子模式日志，输出对比统计（用于校准阈值）
# 用法：.venv\Scripts\python.exe scripts\report_shadow.py [--file logs/group_shadow.jsonl] [--date 2026-11-03]

import sys
import json
import argparse
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")


def load(path: Path, date: str = ""):
    rows = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if date and not str(r.get("ts", "")).startswith(date):
                continue
            rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "logs" / "group_shadow.jsonl"))
    ap.add_argument("--date", default="")
    a = ap.parse_args()

    rows = load(Path(a.file), a.date)
    if not rows:
        print(f"没有影子记录：{a.file}" + (f"（日期 {a.date}）" if a.date else ""))
        print("提示：QQ 服务运行且 qq_bot.shadow.enabled=true 时，群消息会自动记录。")
        return

    total = len(rows)
    decisions = [r.get("decision", {}) for r in rows]
    would = [r for r, d in zip(rows, decisions) if d.get("respond")]

    reasons = Counter(d.get("reason", "?") for d in decisions)
    hours = Counter(r["ts"][11:13] + ":00" for r in rows if r.get("ts"))
    lat = [r.get("latency_ms", 0) for r in rows]

    # 新旧策略对比
    tp = sum(1 for r, d in zip(rows, decisions)
             if r.get("legacy_engage") and d.get("respond"))
    shadow_only = sum(1 for r, d in zip(rows, decisions)
                      if not r.get("legacy_engage") and d.get("respond"))
    legacy_only = sum(1 for r, d in zip(rows, decisions)
                      if r.get("legacy_engage") and not d.get("respond"))
    both_silent = total - tp - shadow_only - legacy_only

    # @ 覆盖率（硬要求：@ 必回）
    at_me = [r for r in rows if r.get("decision", {}).get("features", {}).get("at_me")]
    at_me_missed = [r for r in at_me if not r.get("decision", {}).get("respond")]

    # 擦边分（接近阈值，值得人工看）
    threshold = 0.55
    near = [r for r in rows
            if threshold - 0.2 <= r.get("decision", {}).get("score", 0) < threshold]

    print(f"=== 影子模式报告 {a.date or '全部'} ===")
    print(f"消息总数: {total}")
    print(f"新策略会接: {len(would)} ({len(would) / total * 100:.1f}%)")
    print(f"决策耗时: 平均 {sum(lat) / len(lat):.2f} ms，最大 {max(lat):.2f} ms")
    print(f"\n@ 我 的消息: {len(at_me)} 条，漏回: {len(at_me_missed)} 条"
          + ("  ← 必须有 0" if at_me_missed else "  ✓"))
    if at_me_missed:
        for r in at_me_missed[:5]:
            print("   漏:", r.get("ts"), r.get("text", "")[:40],
                  r.get("decision", {}).get("reason"))

    print("\n原因分布:")
    for reason, n in reasons.most_common():
        print(f"  {reason:22s} {n:4d}  ({n / total * 100:5.1f}%)")

    print("\n按小时:")
    for h in sorted(hours):
        print(f"  {h}  {hours[h]:4d}")

    print("\n新旧策略对比:")
    print(f"  两者都接          {tp:4d}")
    print(f"  仅新策略会接      {shadow_only:4d}   ← 影子带来的新增机会")
    print(f"  仅旧策略会接      {legacy_only:4d}   ← 旧策略的误接嫌疑")
    print(f"  两者都不接        {both_silent:4d}")

    print(f"\n擦边分（{threshold - 0.2:.2f}~{threshold:.2f}，建议人工看）: {len(near)} 条")
    for r in near[:10]:
        d = r.get("decision", {})
        print(f"  {r.get('ts', '')[11:16]} [{d.get('score', 0):.2f}] "
              f"{r.get('sender_name', '')}: {r.get('text', '')[:36]}")

    print("\n提示：阈值在 config/app_config.yaml → qq_bot.shadow.threshold 调整。")


if __name__ == "__main__":
    main()
