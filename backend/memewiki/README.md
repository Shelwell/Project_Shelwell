# backend/memewiki 说明

> 梗百科：判断对方是在“问梗”还是“提到梗”，命中时注入词条与用法规则。
> 原则：宁可漏查，不硬塞；闲聊不塞梗解释。

## 1. 数据

> 当前 `config/app_config.yaml` 中 `memewiki.enabled: false`，梗百科默认关闭；代码和数据保留，打开开关即可恢复。

```text
backend/memewiki/data/memes.db    SQLite 梗库（约 1.6 万条，只读）
```

- 默认路径：`backend/memewiki/data/memes.db`
- 通过 `MemeStore(db_path=...)` 可改
- 只读打开，`check_same_thread=False`，带 512 条 LRU
- 数据缺失时 `available()` 返回 `False`，注入为空，不影响主对话

## 2. 文件

| 文件 | 职责 |
|---|---|
| `matcher.py` | 问梗句式识别、裸词提取、唤醒词剥离、标题扫描 |
| `store.py` | 只读 SQLite + 五级匹配 + 格式化词条 |
| `injector.py` | 命中后组装注入文本（词条在前，规则在后） |

## 3. 接口

```python
from backend.memewiki.injector import build_injection, lookup_tool

text = build_injection("西尔维娅，这是什么梗？", play_level="L1")
# 命中：词条 + 【怎么用这条梗】规则；未命中：空字符串

answer = lookup_tool("芭比Q", play_level="L1")
# 工具调用入口：直接返回词条/未收录提示
```

```python
from backend.memewiki.matcher import extract_query, scan_title_in_text

extract_query(text)        # -> str | None，问梗时才返回
scan_title_in_text(text)   # 标题兜底扫描
```

## 4. 匹配顺序

`MemeStore.lookup()` 从严到宽：

1. 完全一致
2. 别名（逗号分隔，整项精确）
3. 标题以查询开头（取最短）
4. 查询里包含标题（取最长，最具体）
5. 标题里包含查询（≥3 字，或纯数字/字母 ≥2 字）

`matcher` 先判断“像不像在问梗”，避免把普通聊天词当梗查。

## 5. 注入格式

```text
<词条内容>

【怎么用这条梗】
1. 对方在问「什么意思/什么梗」→ 用一两句大白话说清楚含义，别念出处全文；
2. 对方只是提到或想用这个梗 → 直接顺着接，别解释；
3. 别把这条词条原样复述给用户，用你自己的话。
（玩梗尺度：...）
```

- 词条在前，规则在后，避免规则压过内容
- 玩梗尺度复用 persona 的 L0-L3 分级
- 未收录时提示“说不知道/反问”，不硬编

## 6. 接入点

- `ContextManager` 在构建本轮上下文时调用 `build_injection()`
- 命中内容作为动态块注入，不写进 system 前缀
- 工具调用 `lookup_tool()` 供模型主动查询（如果启用了工具）

## 7. 约束

- 梗库是只读数据，不在运行时写入；更新直接替换 `memes.db`
- 不把梗解释塞进每一轮；只在本轮文本命中时注入
- 新增匹配规则先补 `matcher.py` 的测试样例，避免误伤普通聊天
- 不把梗库内容写进长期记忆或知识库
