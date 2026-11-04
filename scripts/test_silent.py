# scripts/test_silent.py
# 职责：验证“模型可 0 输出/沉默”的判定与旁白行为
# 用法：.venv\Scripts\python.exe scripts\test_silent.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.orchestrator import (Orchestrator, is_silent_reply,
                                  strip_silent_marker, DEFAULT_SILENT_HINT)

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


print("[1] 标记判定")
check("空串算沉默", is_silent_reply("") is True)
check("空白算沉默", is_silent_reply("  \n") is True)
check("标准标记", is_silent_reply("[SILENT]") is True)
check("全角标记", is_silent_reply("【silent】") is True)
check("带标点", is_silent_reply(" SILENT。 ") is True)
check("带说明不算沉默", is_silent_reply("[SILENT] 好") is False)
check("正常回复不算沉默", is_silent_reply("嗯") is False)
check("普通回复里的标记会被清理", strip_silent_marker("[SILENT] 好") == "好")


class StubContext:
    def __init__(self):
        self.archived = []

    def prepare_context(self, messages, **kw):
        return list(messages)

    def archive_turn(self, **kw):
        self.archived.append(kw)


def run(raw, query="在吗"):
    ctx = StubContext()
    o = Orchestrator(context_manager=ctx)
    o._call_model = lambda prepared, keep_alive=None: raw
    r = o.chat([{"role": "user", "content": query}], current_query=query,
               speaker_id="1", channel_type="private", speaker_name="小明")
    return r, ctx


print("\n[2] 沉默回合")
r, ctx = run("[SILENT]")
check("ok=True", r["ok"] is True)
check("silent=True", r.get("silent") is True, r)
check("只发旁白", r["bubbles"] == [DEFAULT_SILENT_HINT], r["bubbles"])
check("旁白不归档为 AI 回复", ctx.archived and ctx.archived[-1]["ai_msg"] == "",
      ctx.archived)
check("用户消息仍归档", ctx.archived[-1]["user_msg"] == "在吗")

print("\n[3] 空输出")
r, ctx = run("   ")
check("空输出按沉默处理", r.get("silent") is True and r["bubbles"] == [DEFAULT_SILENT_HINT])

print("\n[4] 正常回合")
r, ctx = run("嗯")
check("silent=False", r.get("silent") is False, r)
check("正常回复", r["reply"] == "嗯", r)
check("正常归档", ctx.archived[-1]["ai_msg"] == "嗯", ctx.archived)

print("\n[5] 标记误带")
r, ctx = run("[SILENT] 好")
check("带正文时不沉默", r.get("silent") is False and r["reply"] == "好", r)

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
