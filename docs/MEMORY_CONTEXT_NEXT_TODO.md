# 下一阶段重点待办：记忆、上下文、工具与遗忘

> 独立清单，不并入现有 TODO。  
> 创建日期：2026-11-10  
> 目标：先保证记忆连续、上下文稳定、隐私不越界，再扩展工具与长期治理。

---

## 总目标

```text
记忆可检索
上下文可缓存
工具可兜底
遗忘可级联
隐私不越界
风格不被过度审查
```

验收只看六件事：

1. 群聊 → 私聊能接上
2. 压缩后还能继续
3. 重启后还能继续
4. 几天前的旧事能查回
5. 私聊不会泄漏到群
6. 删除/遗忘后不会复活

---

# P0：不做会直接影响体验

## 1. Scope / Visibility 数据模型

- [ ] `MemoryEvent` 增加 `scope`、`visibility`、`deleted_at`、`epoch`
- [ ] `SemanticFact` 增加 `scope`、`visibility`、`version`、`superseded_by`、`deleted_at`
- [ ] `Diary` 增加 `source_event_ids`、`status`、`epoch`
- [ ] `Summary` 增加 `covered_event_ids`、`covered_diary_ids`、`version`、`epoch`
- [ ] `Thread` 增加 `participants`、`open_questions`、`visibility`
- [ ] 旧数据迁移：从 `channel_type/channel_id` 回填 `scope/visibility`

**预期结果**

- 群聊只能看到 group
- 私聊能看到 private + group C1
- 私聊不返回群原文
- 群聊不返回私聊摘要
- 新数据不再依赖 `ContextManager` 猜 scope

---

## 2. MemoryPort 统一接口

- [ ] 新增 `build_context(scope, speaker, query, budget)`
- [ ] 返回 `fragments / token_estimate / fits / reason_codes / withheld / epoch`
- [ ] `ContextManager` 不再直接调用多个 memory 方法
- [ ] `ContextManager` 不直接访问 SQLite/Chroma
- [ ] 检索、准入、去重、预算都在 Memory 层完成

**预期结果**

- 上下文层只消费 fragments
- Memory 层可以独立替换/测试
- 未来接 Mnemosyne 时只换 Adapter

---

## 3. Retrieval Planner

- [ ] 固定基线：profile + C1
- [ ] `should_retrieve` 判断：
  - 明确记忆意图
  - 时间表达
  - 未知实体
  - 话题不匹配
  - 长间隔
  - 模型工具调用
- [ ] 混合检索：
  - 向量
  - FTS/关键词
  - 元数据过滤
- [ ] RRF 或加权合并
- [ ] 可选 rerank
- [ ] 低分 abstain，不强行注入
- [ ] 查询改写：时间、人物、当前话题

**预期结果**

- 普通闲聊不触发深度检索
- “上次/之前/那天”能命中
- 不相关记忆不会被硬塞
- 检索失败时不会编造

---

## 4. 上下文组装与缓存

- [ ] 固定顺序：
  ```text
  A 静态人格
  B 群友档案
  C1 日记/摘要
  C2 压缩历史
  D2 最近原文
  E 当前档案/关系/记忆/知识
  F 时间/天气
  当前发言
  ```
- [ ] 当前档案不进 system
- [ ] 天气不进 C1
- [ ] D2 只走 history
- [ ] 删除重复的群上下文块
- [ ] system 只保留稳定前缀
- [ ] 输出预留和 memory 预算分开
- [ ] 记录 `system_sha1`
- [ ] 记录 `prompt_eval_count`

**预期结果**

- 同一个人连续聊天，system hash 不变
- 换人只重算尾部
- 群上下文不破坏 system
- 工具结果不进入 system

---

## 5. C1 / C2 / 日记持久化

- [ ] `_session_summary` 不再作为唯一来源
- [ ] 摘要写 `summaries.jsonl`
- [ ] 日记写 `diaries.jsonl`
- [ ] 压缩历史写 `compressed/<scope>.json`
- [ ] 启动时恢复 C1/C2/thread/epoch
- [ ] 摘要带 `covered_ids`
- [ ] 日记带 `source_event_ids`
- [ ] 支持手动摘要模式

**预期结果**

- 重启后仍能接上之前话题
- 摘要可追溯到原文
- 日记可追溯来源
- 删除事件时能知道哪些摘要/日记要失效

---

## 6. 遗忘级联

- [ ] tombstone：`deleted_at`
- [ ] 删除 event 时级联：
  - semantic facts
  - vector
  - FTS
  - diary
  - summary
  - thread
  - C1 cache
  - C2 compressed
- [ ] 软删 / 硬删区分
- [ ] 硬删需要确认
- [ ] 纠正/替代：旧事实 `valid_to`
- [ ] scope epoch
- [ ] 旧 epoch 缓存失效
- [ ] 工具结果不返回已删内容

**预期结果**

- 用户说“忘掉”后，群聊/私聊/工具都查不到
- 摘要不会继续注入旧内容
- 重启后不会复活
- 删除失败也有 tombstone 兜底

---

## 7. 五场景回归

- [ ] A 群聊 → 私聊继续
- [ ] B 群聊压缩后 A 继续
- [ ] 重启后继续
- [ ] 几天后旧事
- [ ] A 私聊 → B 群聊
- [ ] 私聊内容不泄漏
- [ ] 删除后不复活

**预期结果**

- 每个场景都有固定测试脚本
- 记录必需事实命中率
- 记录错误记忆/泄漏次数
- 记录 prompt token 和延迟

---

# P1：长期体验

## 8. 只读记忆工具

- [ ] `memory_search`
- [ ] `memory_get`
- [ ] `memory_diary`
- [ ] `memory_thread`
- [ ] scope 由服务端注入
- [ ] 工具结果标准化为 fragment
- [ ] 去重、排序、预算
- [ ] 只进本轮尾部
- [ ] 不写长期记忆
- [ ] 失败不重试

**预期结果**

- 工具查得比自动检索更精确
- 工具结果不破坏 system
- 模型不暴露工具过程
- 群聊工具不可能返回私聊

---

## 9. 日记 / 摘要流水线

- [ ] 每天固定时间写日记
- [ ] 日记包含群聊/私聊 section
- [ ] 少于 20 条按策略累计或跳过
- [ ] 每 2 篇日记生成摘要
- [ ] 摘要输入：最近摘要 + 最早未摘要日记
- [ ] 手动审核模式
- [ ] 稳定后自动

**预期结果**

- 日记不依赖 token 压缩
- 摘要不丢最早未摘要内容
- 冷启动可人工纠正
- 重启不丢状态

---

## 10. Topic / Thread

- [ ] 当前话题
- [ ] 参与者
- [ ] 未解决问题
- [ ] 话题来源
- [ ] 跨群/私聊引用规则
- [ ] 私聊可读群 topic
- [ ] 群聊不可读私聊 topic

**预期结果**

- “接着刚才”能定位到话题
- 压缩后话题不丢
- 话题可跨频道但受权限控制

---

## 11. 安全与风格配置

- [ ] `safety.profile: permissive`
- [ ] 允许脏话/毒舌/黑色幽默/角色冲突
- [ ] 不做通用客服化改写
- [ ] 硬红线单独拦截
- [ ] 未成年性内容硬拒
- [ ] 违法操作硬拒
- [ ] 隐私泄漏硬拒
- [ ] 工具结果注入防护
- [ ] 过度拒答统计

**预期结果**

- 角色风格保留
- 不过度拒答
- 硬红线不放松
- 私聊/群聊边界不被风格配置绕过

---

## 12. 记忆管理 UI

- [ ] 查看 scope/visibility
- [ ] 查看 source_ids
- [ ] 纠正事实
- [ ] 软删/硬删
- [ ] 重建摘要/日记
- [ ] 查看 pending 摘要
- [ ] 查看 tombstone
- [ ] 查看 epoch

**预期结果**

- 出问题能定位到来源
- 不用直接改 SQLite
- 删除/纠正有审计

---

# P2：后续增强

- [ ] 多群隔离
- [ ] 多 QQ 同 person_id
- [ ] 图片/语音/文档记忆
- [ ] 未来提醒/承诺
- [ ] 旧记忆按日期/人物检索
- [ ] Mnemosyne Adapter
- [ ] Shadow 对比 / Cutover
- [ ] API 主备切换
- [ ] 备份/恢复/导出

---

# 建议实施顺序

```text
1. Scope/Visibility
2. MemoryPort
3. Retrieval Planner
4. Context 缓存与预算
5. C1/C2/日记持久化
6. 遗忘级联
7. 五场景测试
8. 只读工具
9. Topic/Thread
10. 安全风格
11. UI
12. Mnemosyne/API
```

---

# 预期最终效果

```text
群聊：
  A 静态人格 + B 群友档案 + C1 摘要 + C2 压缩 + D2 最近
  + E 检索/关系 + F 环境 + 当前

私聊：
  私聊 C1/D2
  + 群 C1/B
  + 不读群原文

工具：
  只读、按需、带 scope、带来源、有预算

遗忘：
  有 tombstone、有级联、有 epoch、重启不复活

安全：
  风格宽松，硬红线收紧
```

---

# 一句话

```text
先把 scope/port/retrieval/context/forget 做稳
再做 tools/diary/thread
最后做 UI/Mnemosyne/API
```
