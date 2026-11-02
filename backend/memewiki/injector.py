# backend/memewiki/injector.py
# 职责：构造注入给模型的梗百科文案
# 结构：词条内容在前（权重高）+ 精简规则在后（避免规则压过内容）

from backend.memewiki.matcher import extract_query, scan_title_in_text
from backend.memewiki.store import get_store

# 规则：3 条，明确区分「问含义」和「只是提到」
_RULES = (
    "【怎么用这条梗】\n"
    "1. 对方在问「什么意思/什么梗」→ 用一两句大白话说清楚含义，别念出处全文；\n"
    "2. 对方只是提到或想用这个梗 → 直接顺着接，别解释；\n"
    "3. 别把这条词条原样复述给用户，用你自己的话。"
)


def _play_scale_text(level: str = None) -> str:
    """玩梗尺度，复用 persona 粗口分级（L0-L3）"""
    if not level:
        return ""
    level = str(level).upper()
    mapping = {
        "L0": "把对方当真人尊重，黑称一律不接。",
        "L1": "可以损操作、损发挥，不损长相；别人先玩黑称才跟着接半句。",
        "L2": "按社区语境来，别人玩得开的梗就跟着玩，冲家人的词不带头用。",
        "L3": "别人先甩的黑称可以接着玩，别扫兴，只对真事不自己编新攻击。",
    }
    txt = mapping.get(level)
    return f"（玩梗尺度：{txt}）" if txt else ""


def _resolve_query(text: str) -> str:
    """解析要查的词：先正则，失败则走标题扫描兜底"""
    q = extract_query(text)
    if q:
        return q
    return scan_title_in_text(text)


def build_injection(text: str, play_level: str = None) -> str:
    """
    主入口：判断消息是否涉及梗库，命中则返回注入文本。
    结构：词条在前，规则在后。
    """
    q = _resolve_query(text)
    if not q:
        return ""

    store = get_store()
    if not store.available():
        return ""

    row = store.lookup(q)
    if not row:
        return ""

    parts = [store.format_entry(row)]
    scale = _play_scale_text(play_level)
    rules = _RULES + (scale if scale else "")
    parts.append(rules)
    return "\n\n".join(parts)


def lookup_tool(query: str, play_level: str = None) -> str:
    """工具调用入口：直接查词"""
    q = (query or "").strip()
    if not q:
        return "要查什么？给我一个词。"

    store = get_store()
    if not store.available():
        return "词库不可用（文件缺失或损坏）。"

    row = store.lookup(q)
    if not row:
        return f"词库里没收录「{q}」。别硬解释——说不知道，或者反问一句「这什么梗」。"

    parts = [store.format_entry(row)]
    scale = _play_scale_text(play_level)
    parts.append(_RULES + (scale if scale else ""))
    return "\n\n".join(parts)