# scripts/verify_round2.py
# 职责：第二轮改动冒烟测试——提示词结构 + reply_format 单元断言 + 真实编排链路
# 用法：.venv\Scripts\python.exe scripts\verify_round2.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.context_manager import ContextManager
from backend.orchestrator import Orchestrator
from backend.reply_format import normalize, split_bubbles

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


print("=" * 60)
print("1) 提示词结构")
cm = ContextManager(memory_enabled=False)
si = cm.system_instruction
check("含【玩梗尺度】", "【玩梗尺度】" in si)
check("无数字硬上限规则", "不超过 30 字" not in si and "最多 3 条" not in si)
check("语言风格行已无“滚”", "“滚”" not in si and "、滚" not in si)
check("facts 已注入", "【你熟悉的事" in si and "明日方舟" in si)
check("含放心玩梗授权", "放心玩梗" in si)
print(f"  提示词 {len(si)} 字")

print("=" * 60)
print("2) reply_format 单元断言")
cases = [
    # (输入, 期望气泡, 说明)
    ('"怎么了？是工作上遇到麻烦了吗？', ['"怎么了？是工作上遇到麻烦了吗？'.replace('"', "")], "行首残缺引号"),
    ("嗯\n\n累\n", ["嗯", "累"], "空行与首尾空白"),
    ("**重要**：记得吃饭", ["重要：记得吃饭"], "Markdown 加粗"),
    ("我最近在看 called \"Made in Abyss\" 挺好的", None, "中英混排保留（不硬约束）"),
]
for raw, expect, name in cases:
    got = split_bubbles(raw)
    if expect is None:
        check(name, len(got) >= 1 and "Made in Abyss" in got[0], str(got))
    else:
        check(name, got == expect, f"got={got}")

long_line = "今天先别想太多了，早点睡，明天起来再说，工作的事一件一件来，实在不行我陪你骂老板"
b = split_bubbles(long_line, limit=20, max_bubbles=3)
check("超长单行被切分", len(b) > 1, str(b))
check("气泡数不超上限", len(b) <= 3, str(b))
check("切分不丢内容", "".join(b) == long_line.replace("\n", ""), f"joined={'|'.join(b)}")

many = "\n".join([f"第{i}句" for i in range(1, 7)])
b = split_bubbles(many, limit=40, max_bubbles=3)
check("超量气泡合并", len(b) == 3, str(b))
check("合并后不丢内容", "第6句" in b[-1], str(b))

print("=" * 60)
print("3) 真实编排链路（27B）")
orc = Orchestrator(ContextManager(memory_enabled=False))
probes = [
    ("情绪", "在吗，今天加班到十点，累死了"),
    ("低落", "我觉得我最近干什么都不行"),
    ("推荐", "有啥动画推荐吗"),
]
for tag, q in probes:
    r = orc.chat([{"role": "user", "content": q}], session_id="verify")
    if not r.get("ok"):
        check(f"{tag} 调用成功", False, r.get("error", ""))
        continue
    bubbles = r.get("bubbles") or []
    print(f"\n[{tag}] {q}")
    for i, bb in enumerate(bubbles, 1):
        print(f"   气泡{i}({len(bb)}字): {bb}")
    check(f"{tag} 有气泡", len(bubbles) >= 1)
    check(f"{tag} 气泡数不超限", len(bubbles) <= 3, str(len(bubbles)))
    # 均衡分配下允许单条偏长，但不能出现一条顶三条的极端长尾
    lens = [len(x) for x in bubbles]
    avg = sum(lens) / len(lens)
    check(f"{tag} 气泡长度均衡", max(lens) <= avg * 1.6 + 20, str(lens))

print("\n" + "=" * 60)
print("全部通过" if not FAIL else f"失败项: {FAIL}")
sys.exit(1 if FAIL else 0)
