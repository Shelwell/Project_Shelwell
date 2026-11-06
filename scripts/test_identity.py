# scripts/test_identity.py
# 职责：验证新名字/新触发词已贯穿提示词与代码，且旧名不再出现
# 用法：.venv\Scripts\python.exe scripts\test_identity.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.config_loader import get
from backend.persona_engine import PersonaEngine
from backend.memory.models import MemoryEvent, now_iso, gen_id

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


p = PersonaEngine()
print("[1] 人格名")
check("显示名是西尔维娅", p.display_name == "西尔维娅", p.display_name)
check("全名正确", p.full_name == "西尔维娅·希尔薇", p.full_name)
check("提示词含新名", "西尔维娅" in p.prompt)
check("提示词不含旧名 Shelwell", "Shelwell" not in p.prompt)
check("提示词不含旧昵称", "希儿" not in p.prompt and "小希" not in p.prompt)
check("意识维持装置设定已进入提示词", "意识维持装置" in p.prompt)
check("旧义体设定未残留", "大半还没修好" not in p.prompt)

print("\n[2] 触发词与别名")
triggers = get("qq_bot.trigger_keywords", [])
aliases = get("qq_bot.group_gate.name_aliases", [])
want = ["西尔维娅", "西儿", "小西", "维娅"]
check("触发词正确", triggers == want, triggers)
check("门禁别名正确", aliases == want, aliases)

from backend.group_policy import DEFAULTS
check("门禁默认别名正确", DEFAULTS["name_aliases"] == want, DEFAULTS["name_aliases"])

from backend.memewiki.matcher import _WAKE_WORDS
check("梗百科唤醒词正确", _WAKE_WORDS == want, _WAKE_WORDS)

print("\n[3] 记忆标签")
ev = MemoryEvent(id=gen_id("epi"), timestamp=now_iso(), session_id="qq:1",
                 user_msg="在吗", ai_msg="嗯")
check("合并文本用新名", "西尔维娅：嗯" in ev.combined_text, ev.combined_text)
check("合并文本不含旧名", "Shelwell" not in ev.combined_text)

print("\n[4] 沉默提示词")
from backend.context_manager import ContextManager
fmt = ContextManager.OUTPUT_FORMAT
check("提示词有沉默段落", "【沉默】" in fmt)
check("提示词给出标记", "[SILENT]" in fmt)

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
