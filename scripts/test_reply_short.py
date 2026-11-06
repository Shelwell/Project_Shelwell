# scripts/test_reply_short.py
# 职责：回复气泡合并规则回归测试（多数回复应合并成一条）
# 用法：.venv\Scripts\python.exe scripts\test_reply_short.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.reply_format import split_bubbles, normalize

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


print("[1] 短回复合并成一条")
b = split_bubbles("嗯\n累", limit=40, max_bubbles=2, single_max=45)
check("两段短句合并", b == ["嗯，累"], b)
b = split_bubbles("又加班\n早点睡", limit=40, max_bubbles=2, single_max=45)
check("合并后无换行", all("\n" not in x for x in b), b)
b = split_bubbles("好", limit=40, max_bubbles=2, single_max=45)
check("单字保持单字", b == ["好"], b)

print("\n[2] 超长回复仍拆条（不截断）")
long_text = "今天先别想太多了，早点睡，明天起来再说，工作的事一件一件来，实在不行我陪你骂老板"
b = split_bubbles(long_text, limit=40, max_bubbles=2, single_max=45)
check("拆成多条", len(b) <= 2 and len(b) >= 1, b)
check("不丢内容", "".join(b).replace("，", "") == long_text.replace("，", ""),
      "".join(b))

print("\n[3] 气泡内不得有换行（QQ 按换行拆条）")
b = split_bubbles("第一句\n第二句\n第三句", limit=10, max_bubbles=2, single_max=0)
check("每条都不含换行", all("\n" not in x for x in b), b)

print("\n[4] 关闭合并开关时保持原行为")
b = split_bubbles("嗯\n累", limit=40, max_bubbles=2, single_max=0)
check("single_max=0 不合并", b == ["嗯", "累"], b)

print("\n[5] 空输入")
check("空串", split_bubbles("", single_max=45) == [])
check("只有符号", split_bubbles("。。。", single_max=45) == [])

print("\n[6] 清洗仍生效")
b = split_bubbles('"怎么了？', single_max=45)
check("残缺引号被清掉", b and not b[0].startswith('"'), b)

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
