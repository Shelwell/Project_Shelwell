# backend/memewiki/matcher.py
# 职责：判断一条消息是否「像在问梗」，并提取要查的词
# 原则：宁可漏查，不硬塞。闲聊不该被塞梗解释。

import re

# 问梗句式：以「什么梗/什么意思/怎么用」等结尾
_ASK_PATTERN = re.compile(
    r"(?:是)?(?:什么梗|啥梗|什么来头|什么来历|什么出处|什么意思|啥意思|"
    r"怎么用|怎么接|怎么玩|如何用)(?:啊|呀|呢|的|吗|这是|来着)?[？?。！!~～]*$"
)

# 裸词模式：2-10 个纯中文/字母/数字/间隔点
_BARE_PATTERN = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9·]{2,10}$")

# 无语义词：单独出现没意义
_VAGUE_WORDS = {
    "这", "那", "它", "他", "她", "这个", "那个", "你",
    "这句话", "那句话", "上面", "刚才", "下文", "前面",
}

# 停用词：常见闲聊，别当梗查
_STOP_WORDS = {
    "在吗", "在不在", "哈哈", "哈哈哈", "笑死", "好的", "好吧",
    "晚安", "早安", "睡觉", "吃饭", "干嘛", "没事", "收到",
    "谢谢", "好耶", "草", "典", "乐",
}

# 闲聊短语：整句明显不是问梗
_CHAT_PHRASES = {
    "我是谁", "错了", "情绪", "接下一句", "你也学一个",
    "今天看什么", "到底多少了", "搜出来了吗", "来点贴吧评价",
    "都是同龄人", "你同事", "道德这么高", "这是谁的手臂",
    "比我能串", "宝宝你醒了", "中国人能不能飞", "你现在什么人格",
}

# 句子成分：含这些说明是完整问句，不是单纯问梗
_SENTENCE_PARTS = re.compile(
    r"怎么样|怎么|多少|为什么|能不能|是不是|什么|了吗|了没|一下|你说|要不要|有没有"
)

# 唤醒词：西尔维娅的称呼，先剥掉再判断
_WAKE_WORDS = ["西尔维娅", "西儿", "小西", "维娅"]


def _clean_text(text: str) -> str:
    """去掉 @ 前缀和唤醒词，返回干净的正文"""
    t = str(text or "").strip()
    # 去 @某人
    t = re.sub(r"^(@\S+\s*)+", "", t)
    # 去唤醒词前缀
    for w in _WAKE_WORDS:
        if t.startswith(w):
            rest = t[len(w):].strip()
            rest = re.sub(r"^[，,。.！!？?:：]+|[，,。！!？?:：]+$", "", rest)
            t = rest
            break
    return t.strip()


def extract_query(text: str):
    """
    从消息里提取要查的梗词。
    不是问梗返回 None。
    """
    t = _clean_text(text)
    if not t:
        return None

    # 路径 1：问梗句式
    m = _ASK_PATTERN.search(t)
    if m:
        head = t[:m.start()]
        head = re.sub(r"^[ ，,。.！!？?]+|[ ，,。.！!？?]+$", "", head)
        head = re.sub(r"^[是]+|[是]+$", "", head).strip()
        head = re.sub(r"(这个梗|那个梗|这梗|那梗|这个|那个)$", "", head).strip()
        head = re.sub(r"(到底|究竟)$", "", head).strip()
        # 剥离常见疑问前缀（「你知道要啥自行车」→「要啥自行车」）
        head = re.sub(
            r"^(你知道|你可知|你知不知道|请问|问一下|问问|想问|我想问|"
            r"有没有人知道|有人知道|谁懂|谁能告诉我)",
            "", head).strip()
        head = re.sub(r"^[，,。.！!？?：:的]+", "", head).strip()
        if head in _VAGUE_WORDS:
            return None
        if len(head) <= 2 and head[:1] in "这那它他她":
            return None
        # 明确问梗句式下，允许单字（如「典」「乐」这类单字梗）；
        # 无意义单字已被 _VAGUE_WORDS 过滤
        if 1 <= len(head) <= 12:
            return head
        return None

    # 路径 2：裸词（整句就是一个词）
    if _BARE_PATTERN.match(t):
        if t in _STOP_WORDS or t in _CHAT_PHRASES:
            return None
        if _SENTENCE_PARTS.search(t):
            return None
        return t

    return None


def scan_title_in_text(text: str, store=None) -> str:
    """
    兜底路径：从消息里扫描已收录的梗名。
    做法：把消息切成 3-8 字的连续片段（去标点），批量查词库标题，取最长命中。
    用于「你知道XX吗」「XX听过没」这类正则覆盖不到的句式。
    返回命中的标题，无命中返回空串。
    """
    if store is None:
        from backend.memewiki.store import get_store
        store = get_store()

    t = _clean_text(text)
    if not t or len(t) < 3:
        return ""

    # 提取连续的中文/字母数字片段（按标点切）
    segments = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", t)
    if not segments:
        return ""

    # 生成候选：每个片段取 3-8 字的所有窗口
    candidates = set()
    for seg in segments:
        n = len(seg)
        for size in range(3, 9):
            for i in range(0, n - size + 1):
                candidates.add(seg[i:i + size])
    if not candidates:
        return ""

    # 批量查词库，取最长命中
    hits = store.batch_lookup_titles(list(candidates))
    if not hits:
        return ""
    return max(hits, key=len)


def classify(text: str, store=None) -> tuple:
    """
    判断消息里的梗属于哪种情况，返回 (query, mode)。
    mode:
      "ask"     —— 明确在问梗（「XX是什么意思」），纯查词，不该归档
      "mention" —— 聊天中提及已收录的梗（「你这不是要啥自行车吗」），是真实对话，该归档
      None      —— 没提到梗
    """
    if store is None:
        from backend.memewiki.store import get_store
        store = get_store()

    # 先试正则（问梗句式/裸词）→ 候选词要能查到才算 ask
    q = extract_query(text)
    if q and store is not None:
        cleaned = _clean_text(text)
        is_ask_sentence = bool(_ASK_PATTERN.search(cleaned))
        if is_ask_sentence:
            # 问梗句式：候选词是用户明确指向的，用宽容匹配（支持前缀/子串，如 666→666烧炭）
            if store.lookup(q):
                return q, "ask"
        else:
            # 裸词路径：容易误收整句，必须精确等于词库标题
            if store.batch_lookup_titles([q]):
                return q, "ask"

    # 再试标题扫描（聊天中提及）→ mention
    t = scan_title_in_text(text, store)
    if t:
        return t, "mention"
    return None, None
