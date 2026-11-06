# scripts/test_context_freeze.py
# 职责：验证 system 冻结 + 动态内容下沉（前缀缓存命中的前提）
# 用法：.venv\Scripts\python.exe scripts\test_context_freeze.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.context_manager import ContextManager

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


class StubMemory:
    """最小内存桩：返回固定语义事实，用来验证动态内容的位置"""
    def __init__(self, fact_text):
        self.fact_text = fact_text

    def search_semantic(self, query, top_k=5, owner_id=""):
        # 语义无命中 → 走情景回退路径，验证动态内容仍然下沉到 user 尾部
        return []

    def search(self, *a, **kw):
        return [{"content": self.fact_text,
                 "metadata": {"timestamp": "2026-11-04T10:00:00"}}]

    def get_group_context(self, channel_id, limit=10):
        return [{"speaker": "小明", "text": "今天加班", "is_bot": False}]

    def get_stats(self):
        return {"total_memories": 1}


cm = ContextManager(memory_enabled=False)
cm.memory_enabled = True
cm.memory = StubMemory("用户喜欢喝冰美式")

msgs = [{"role": "user", "content": "在吗"}]
out1 = cm.prepare_context(msgs, current_query="在吗", session_id="qq:1",
                          system_as_role=True)
system1 = out1[0]["content"]
last1 = out1[-1]["content"]

check("第一条是 system", out1[0]["role"] == "system", out1[0]["role"])
check("system 以静态人格开头", system1.startswith(cm.system_instruction),
      f"{len(system1)} vs {len(cm.system_instruction)}")
check("C1 天气摘要进入 system", "【天气】" in system1)
check("语义记忆不在 system 里", "冰美式" not in system1)
check("动态内容在本轮 user 里", "冰美式" in last1, last1[:80])
check("user 原文仍在", "在吗" in last1)

# 换一条事实再跑：system 必须字节级不变，user 尾部变化
cm.memory = StubMemory("用户下周去成都")
out2 = cm.prepare_context(msgs, current_query="在吗", session_id="qq:1",
                          system_as_role=True)
check("第二轮 system 不变", out2[0]["content"] == system1)
check("第二轮动态内容更新", "成都" in out2[-1]["content"])

# 群聊场景：群上下文也应落在 user 尾部
out3 = cm.prepare_context([{"role": "user", "content": "接话吗"}],
                          current_query="接话吗", session_id="qqg:9:1",
                          channel_type="group", channel_id="9",
                          speaker_hint="当前发言者：小明",
                          system_as_role=True)
check("群上下文不在 system", "群里刚才在聊" not in out3[0]["content"])
check("群上下文在 user 尾部", "群里刚才在聊" in out3[-1]["content"])
check("群名单不带条数", "（1条）" not in out3[-1]["content"])
check("当前发言者在 user 尾部", "当前发言者：小明" in out3[-1]["content"])

# 图片消息：下沉不能破坏 images 字段
out4 = cm.prepare_context([{"role": "user", "content": "看看这个", "images": ["AAA"]}],
                          current_query="看看这个", session_id="qq:1",
                          system_as_role=True)
check("images 字段保留", out4[-1].get("images") == ["AAA"], out4[-1].keys())

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
