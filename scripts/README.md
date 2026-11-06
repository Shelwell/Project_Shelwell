# scripts 说明

> 开发/运维脚本与回归测试。全部从项目根目录运行 `.venv\Scripts\python.exe`。

## 1. 环境

```powershell
cd E:\Shelwell
.venv\Scripts\python.exe scripts\test_identity.py
```

- 需要项目根在 `sys.path`；脚本自己会插入
- 涉及 Ollama 的测试在服务未启动时应跳过或降级，不应阻塞
- 测试多用临时目录，不污染 `memory/`；少数会写 `logs/llm_trace`，跑完可清

## 2. 工具

| 脚本 | 用途 | 典型命令 |
|---|---|---|
| `build_knowledge_jsonl.py` | Markdown → RAG 分块 JSONL | `python scripts/build_knowledge_jsonl.py` |
| `ingest_knowledge.py` | 知识库写入 Chroma | `python scripts/ingest_knowledge.py` |
| `rebuild_index.py` | 重建情景向量/FTS 索引 | `python scripts/rebuild_index.py` |
| `manage_memory.py` | 命令行查看/清理记忆 | `python scripts/manage_memory.py --help` |
| `report_shadow.py` | 分析门禁 shadow 日志 | `python scripts/report_shadow.py` |
| `set_ollama_env.ps1` | 给 NSSM 的 Ollama 服务写环境变量（q8 KV、FlashAttention、12 线程） | `powershell -File scripts/set_ollama_env.ps1` |
| `start_all.ps1` | 一键启动服务/容器（无控制台时） | `powershell -File scripts/start_all.ps1` |
| `ab_prompt_test.py` | 提示词 A/B 对比 | `python scripts/ab_prompt_test.py` |
| `verify_round2.py` | 第二轮行为验证 | `python scripts/verify_round2.py` |
| `generate_file_tree.py` | 生成自动文件树 `文件树.auto.md`（不覆盖人工索引） | `python scripts/generate_file_tree.py` |

> `generate_file_tree.py` 生成 `文件树.auto.md`；**带功能备注的人工索引以根目录 [../文件树.md](../文件树.md) 为准**。

## 3. 回归测试

### 人格/上下文

| 脚本 | 验证 |
|---|---|
| `test_identity.py` | 全名、昵称、触发词、提示词一致性 |
| `test_context_freeze.py` | system 前缀冻结、动态内容下沉到本轮 user |
| `test_silent.py` | `[SILENT]`/空输出 → 旁白，不进 AI 记忆 |
| `test_reply_short.py` | 短回复合并、长回复切分、气泡上限 |

### 记忆

| 脚本 | 验证 |
|---|---|
| `test_memory_retrieval.py` | 情景召回/相似度/归属过滤 |
| `test_memory_edit.py` | 双击编辑、重写 JSONL、重建索引 |
| `test_memory_delete.py` | 删除情景/会话、语义引用清理 |
| `test_auto_consolidate.py` | 自动提炼时间、标记、失败降级 |
| `test_extractor_identity.py` | 事实归属不串人 |
| `test_knowledge.py` | 世界知识检索 |
| `test_knowledge_crud.py` | 知识 JSONL/Chroma 添加、更新、删除、导入、重建 |

### 关系/群聊

| 脚本 | 验证 |
|---|---|
| `test_relationship.py` | 5 级阈值、24h+1、非指定对象、双计数、回填 |
| `test_gate_runner.py` | 门禁 active/shadow、日志、冷却 |
| `test_group_shadow.py` | 门禁规则评分（@/回复/热聊/静默） |
| `test_group_state.py` | 状态机 IDLE/WATCH/QUEUED/COOLDOWN、合并、冷却 |

### 视觉

| 脚本 | 验证 |
|---|---|
| `test_vision.py` | 小图识别、缓存、限频、失败降级 |

### 天气

| 脚本 | 验证 |
|---|---|
| `test_weather.py` | 9 档时间、雪季、跨日固定、重启固定、温度范围 |

## 4. 全量回归

```powershell
$tests = @(
  "test_identity", "test_context_freeze", "test_silent", "test_reply_short",
  "test_memory_retrieval", "test_memory_edit", "test_memory_delete",
  "test_auto_consolidate", "test_extractor_identity", "test_knowledge",
  "test_knowledge_crud", "test_relationship", "test_gate_runner", "test_group_shadow",
  "test_group_state", "test_vision", "test_weather"
)
foreach ($t in $tests) {
  .venv\Scripts\python.exe "scripts\$t.py"
  if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $t"; break }
}
```

- 需要 Ollama 的用例在服务未启动时可能跳过/降级；看输出里的 `SKIP`
- 跑完全量后建议清一次 `logs/llm_trace`（控制台“调试 → 清空 trace”）

## 5. 数据准备

| 文件 | 用途 |
|---|---|
| `data_prep/generate_persona_data.py` | 生成人格训练样本 |
| `data_prep/persona_seed.jsonl` | 历史人格种子数据 |

训练依赖见根目录 `requirements-train.txt`；产出放 `data/`（gitignore）。

## 6. 新增脚本

- 工具脚本放 `scripts/`，测试命名 `test_*.py`
- 脚本头写：职责、用法、依赖
- 测试必须能在无 QQ/无 NapCat 的环境跑；模型相关要么 mock，要么可跳过
- 新增测试后更新本文件“回归测试”表
