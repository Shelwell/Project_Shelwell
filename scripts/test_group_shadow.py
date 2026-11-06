# scripts/test_group_shadow.py
# 职责：影子模式决策规则回归测试（纯本地，不连模型/QQ）
# 用法：.venv\Scripts\python.exe scripts\test_group_shadow.py

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.group_policy import GroupGate, GroupMessage, load_interest_terms

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


def ts_at(hour, minute=0, second=0):
    """构造指定时刻的时间戳（本地时区）"""
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, second, 0, 0, -1))


def gate(**over):
    cfg = {"active_hours": ["19:00-24:00"], "quiet_hours": ["00:30-09:00"],
           "name_aliases": ["西尔维娅", "西儿", "小西", "维娅"]}
    cfg.update(over)
    return GroupGate(cfg, interest_terms=["明日方舟", "火锅", "成都"])


def msg(text, ts, **kw):
    base = dict(group_id="g1", sender_id="1001", sender_name="小明", text=text, ts=ts)
    base.update(kw)
    return GroupMessage(**base)


print("[1] 明确对我：必回")
g = gate()
d = g.observe(msg("在吗", ts_at(3), at_me=True))          # 静默时段也应回
check("@我 无视静默时段", d.respond and d.reason == "at_me", d.to_dict())
g = gate()
check("称呼命中（西尔维娅）", g.observe(msg("西尔维娅 你在吗", ts_at(21))).reason == "at_me")
check("中文称呼命中（西儿）", g.observe(msg("西儿来看看", ts_at(21))).reason == "at_me")
check("普通消息不误判",
      g.observe(msg("今天天气不错", ts_at(21))).reason != "at_me")

print("\n[2] 回复我 = 明确对话")
g = gate()
d = g.observe(msg("刚才那个挺好", ts_at(21), reply_to_sender="西尔维娅"))
check("引用我的消息", d.respond and d.reason == "reply_to_me", d.to_dict())
g = gate()
d = g.observe(msg("刚才那个挺好", ts_at(3), reply_to_sender="西尔维娅"))
check("引用我在深夜也回", d.respond, d.to_dict())

print("\n[3] 不该接的情况")
g = gate()
check("静默时段普通消息", g.observe(msg("有人在吗", ts_at(3))).reason == "quiet_hours")
g = gate()
check("@别人 不接", g.observe(msg("@张三 你看呢", ts_at(21), at_other=True)).reason
      == "addressed_to_other")
g = gate()
check("太短不接", g.observe(msg("哈", ts_at(21))).reason == "too_short")

print("\n[4] 热聊与冷却")
g = gate()
g.observe(msg("今天吃啥", ts_at(21, 0, 0), sender_id="A"))
g.observe(msg("火锅吧", ts_at(21, 0, 5), sender_id="B"))
d = g.observe(msg("火锅走起", ts_at(21, 0, 10), sender_id="A"))
check("多人快速交替 → 热聊不插", d.reason == "hot_chat", d.to_dict())

g = gate()
first = g.observe(msg("有人推荐个番吗", ts_at(21, 0, 0)))
second = g.observe(msg("最近没番看", ts_at(21, 0, 20)))
check("首次提问可接", first.respond, first.to_dict())
check("冷却期内不接", second.reason == "cooldown", second.to_dict())

print("\n[5] 计分：提问 / 兴趣 / 阈值")
g = gate()
d = g.observe(msg("这周末有人去成都吗", ts_at(20)))
check("开放式提问过阈值", d.respond and d.reason == "open_question", d.to_dict())

g = gate()
d = g.observe(msg("明日方舟今天更新了", ts_at(20)))
check("仅命中兴趣不过阈值（宁漏）", not d.respond, d.to_dict())

g = gate(threshold=0.95)
d = g.observe(msg("这周末有人去成都吗", ts_at(20)))
check("阈值调高后提问也不接", not d.respond, d.to_dict())

g = gate()
d = g.observe(msg("今天好无聊啊", ts_at(20)))
check("无信号沉默", not d.respond and d.reason == "no_signal", d.to_dict())

print("\n[6] 兴趣词表")
terms = load_interest_terms()
check("facts 兴趣词已加载", len(terms) >= 5, len(terms))
check("包含明日方舟", "明日方舟" in terms, terms[:10])

print("\n[7] 状态与记录")
g = gate()
g.on_bot_spoke("g1", "我也觉得火锅好吃")
d = g.observe(msg("火锅确实香", ts_at(20)))
check("机器人发言参与话题相似度", d.features["topic_sim"] > 0, d.features)
check("决策含延迟无关字段", "legacy_engage" in d.features)

print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
