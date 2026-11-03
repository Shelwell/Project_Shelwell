# backend/reply_format.py
# 职责：对话后处理——清洗模型输出，并按气泡切分（格式由代码负责，模型只管内容）
# 归属：L4 编排层的后处理步骤，由 orchestrator 调用，QQ 适配层按换行逐条发送

import re

# 零宽与不可见字符
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)

# 句子结束符（用于超长气泡的切分点，保留标点）
_SENT_END = re.compile(r"(?<=[。！？!?；;…])")

# 中文标点集合（用于清理标点前的多余空格）
_CJK_PUNCT = "，。！？；：、）】》"


def _strip_stray_quotes(line: str) -> str:
    """去掉不配对的引号（模型偶尔会在行首/行尾多吐一个引号）"""
    if not line:
        return line
    # 直引号：数量为奇数说明有残缺
    if line.count('"') % 2 == 1:
        if line.startswith('"'):
            line = line[1:]
        elif line.endswith('"'):
            line = line[:-1]
    # 弯引号：左右数量不等
    if line.count("“") > line.count("”") and line.startswith("“"):
        line = line[1:]
    if line.count("”") > line.count("“") and line.endswith("”"):
        line = line[:-1]
    if line.count("‘") > line.count("’") and line.startswith("‘"):
        line = line[1:]
    if line.count("’") > line.count("‘") and line.endswith("’"):
        line = line[:-1]
    return line


def normalize(text: str) -> str:
    """
    清洗模型原始输出：
    - 零宽字符、字面 \\n、Markdown 噪音
    - 不配对引号、多余空格、标点前空格
    - 三个以上连续重复的句末标点
    注意：不限制中英混排（英文缩写、作品名是正常表达）
    """
    if not text:
        return ""
    t = text.translate(_INVISIBLE)
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\\n", "\n")

    # Markdown 噪音：加粗、行内代码、标题
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)

    lines = []
    for raw in t.split("\n"):
        line = raw.strip()
        if not line:
            continue
        line = _strip_stray_quotes(line)
        line = re.sub(r"[ \t]+", " ", line)
        line = re.sub(r"\s+(?=[" + _CJK_PUNCT + r"])", "", line)
        line = re.sub(r"(?<=[（【《])\s+", "", line)
        line = re.sub(r"([。！？!?])\1{2,}", r"\1", line)
        line = line.strip()
        # 只剩标点/引号的行直接丢掉
        if line and re.search(r"[\w\u4e00-\u9fa5]", line):
            lines.append(line)
    return "\n".join(lines)


def _hard_wrap(s: str, limit: int) -> list:
    """单句仍超长时按逗号兜底硬切"""
    out = []
    while len(s) > limit:
        cut = max(s.rfind("，", 0, limit), s.rfind(",", 0, limit), s.rfind("、", 0, limit))
        if cut <= 0:
            cut = limit
        else:
            cut += 1
        out.append(s[:cut])
        s = s[cut:]
    if s:
        out.append(s)
    return out


def _split_sentences(line: str, limit: int) -> list:
    """把超长的一行按句末标点切成不超过 limit 的若干段"""
    parts = [p for p in _SENT_END.split(line) if p]
    out, buf = [], ""
    for p in parts:
        if len(buf) + len(p) <= limit:
            buf += p
            continue
        if buf:
            out.append(buf)
            buf = ""
        if len(p) > limit:
            out.extend(_hard_wrap(p, limit))
        else:
            buf = p
    if buf:
        out.append(buf)
    return out


def split_bubbles(text: str, limit: int = 40, max_bubbles: int = 3) -> list:
    """
    把模型输出切成 QQ 气泡列表。

    顺序：先按换行（模型自己的断句）→ 超长行按标点再切 → 超出上限的部分并入最后一条，
    保证不丢内容。返回的每一项都应是独立的一条消息。
    """
    text = normalize(text)
    if not text:
        return []

    pieces = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(line) <= limit:
            pieces.append(line)
        else:
            pieces.extend(_split_sentences(line, limit))

    if len(pieces) > max_bubbles:
        # 内容超出气泡容量：按段数均衡分配到 max_bubbles 条，避免最后一条过长，
        # 同时保证一个字都不丢（宁可单条偏长，也不截断回复）
        per = len(pieces) / max_bubbles
        groups, start = [], 0
        for i in range(max_bubbles):
            end = int(round((i + 1) * per))
            end = max(end, start + 1) if start < len(pieces) else start
            groups.append("".join(pieces[start:end]))
            start = end
        if start < len(pieces):
            groups[-1] += "".join(pieces[start:])
        pieces = [g for g in groups if g]

    return [p.strip() for p in pieces if p.strip()]
