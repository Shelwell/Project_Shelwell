# scripts/test_context_freeze.py
# 职责：验证 system 冻结 + 动态内容下沉（前缀缓存命中的前提）
# 用法：.venv\Scripts\python.exe scripts\test_context_freeze.py

import sys
from pathlib import Path
from types import SimpleNamespace

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
    def list_semantic_facts(self, page=1, page_size=20,
                            owner_id="", state="", keyword=""):
        return {"items": [
            SimpleNamespace(
                fact=f"[{owner_id}]喜欢猫",
                subject=f"[{owner_id}]",
                tag="preference",
                base_weight=0,
                hits=0,
                last_hit="",
                last_activated_at="",
                created_at="",
                archived=False,
            )
        ]}

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
check("天气不在 system", "【天气】" not in system1)
check("语义记忆不在 system 里", "冰美式" not in system1)
check("动态内容在本轮 user 里", "冰美式" in last1, last1[:80])
check("user 原文仍在", "在吗" in last1)

# 换一条事实再跑：system 必须字节级不变，user 尾部变化
cm.memory = StubMemory("用户下周去成都")
out2 = cm.prepare_context(msgs, current_query="在吗", session_id="qq:1",
                          system_as_role=True)
check("第二轮 system 不变", out2[0]["content"] == system1)
check("第二轮动态内容更新", "成都" in out2[-1]["content"])

# 群聊场景：D2 由 history 承载，不再额外拼【群里刚才在聊】
out3 = cm.prepare_context([{"role": "user", "content": "接话吗"}],
                          current_query="接话吗", session_id="qqg:9:1",
                          channel_type="group", channel_id="9",
                          speaker_hint="当前发言者：小明",
                          system_as_role=True)
check("群上下文块不再进 system", "群里刚才在聊" not in out3[0]["content"])
check("群上下文块不再重复拼", "群里刚才在聊" not in out3[-1]["content"])
check("群聊 D2 原文保留", "接话吗" in out3[-1]["content"])
check("当前发言者在 user 尾部", "当前发言者：小明" in out3[-1]["content"])

# C1 准入：私聊可看群背景 + 私聊摘要；群聊不能看私聊/常驻群摘要
out_priv = cm.prepare_context(
    [{"role": "user", "content": "私聊在吗"}],
    current_query="私聊在吗", session_id="qq:1001",
    channel_type="private", channel_id="1001",
    resident_group_id="9", resident_group_summary="群背景摘要",
    session_summary="私聊摘要", system_as_role=True)
sys_priv = out_priv[0]["content"]
check("私聊含群背景摘要", "群背景摘要" in sys_priv)
check("私聊含私聊摘要", "私聊摘要" in sys_priv)
check("私聊不拼群原文", "今天加班" not in sys_priv)

out_group = cm.prepare_context(
    [{"role": "user", "content": "群在吗"}],
    current_query="群在吗", session_id="qqg:9:1",
    channel_type="group", channel_id="9",
    resident_group_id="9", resident_group_summary="私聊不应出现",
    session_summary="群摘要", system_as_role=True)
sys_group = out_group[0]["content"]
check("群聊含群摘要", "群摘要" in sys_group)
check("群聊不含常驻群摘要", "私聊不应出现" not in sys_group)
check("群聊不含私聊原文", "私聊在吗" not in sys_group)

# 图片消息：下沉不能破坏 images 字段
out4 = cm.prepare_context([{"role": "user", "content": "看看这个", "images": ["AAA"]}],
                          current_query="看看这个", session_id="qq:1",
                          system_as_role=True)
check("images 字段保留", out4[-1].get("images") == ["AAA"], out4[-1].keys())

out_a = cm.prepare_context(
    [{"role": "user", "content": "在吗"}],
    current_query="在吗",
    speaker_id="1001",
    system_as_role=True,
)

out_b = cm.prepare_context(
    [{"role": "user", "content": "在吗"}],
    current_query="在吗",
    speaker_id="1002",
    system_as_role=True,
)

check("换人 system 不变", out_a[0]["content"] == out_b[0]["content"])
check("当前档案不在 system",
      "当前说话人的稳定档案" not in out_a[0]["content"])
check("当前档案在最后 user",
      "当前说话人的稳定档案" in out_a[-1]["content"])
check("不同人的档案不同",
      "1001" in out_a[-1]["content"] and "1002" in out_b[-1]["content"])

private_fact = SimpleNamespace(visibility="private:1001")
group_fact = SimpleNamespace(visibility="group:9")
check("私聊事实不进群", cm._fact_allowed(private_fact, "group") is False)
check("群事实可进群", cm._fact_allowed(group_fact, "group") is True)
check("群事实可进私聊", cm._fact_allowed(group_fact, "private") is True)

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
