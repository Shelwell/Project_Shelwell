# desktop 说明

> L5 桌面控制台：PySide6 单机应用，替代 Web 控制台，零端口。
> 只做展示和操作编排；业务逻辑在 `services.py`，底层在 `backend/`。

## 1. 运行

源码：

```powershell
.venv\Scripts\python.exe desktop\main.py
```

打包版：

```text
dist\ShelwellConsole\ShelwellConsole.exe
```

## 2. 文件

| 文件 | 职责 |
|---|---|
| `main.py` | 主窗口、首页服务状态、导航、页面堆栈 |
| `worker.py` | 线程池 + Qt 信号，所有阻塞调用在这里跑 |
| `services.py` | 控制台业务层：服务启停、模型、记忆、关系、API、trace |
| `runtime_hook_ssl.py` | 打包后 SSL 证书路径修正 |
| `ShelwellConsole.spec` | PyInstaller onedir 配置（含管理员 manifest） |
| `pages/*.py` | 各功能页 |

## 3. 页面

| 页面 | 功能 |
|---|---|
| 首页 | 启动/停止 Ollama、QQBot、NapCat；服务/端口/日志状态 |
| 人格管理 | persona 切换、facts 切换、LoRA、新建/删除人格 |
| 记忆管理 | 情景/语义查看、搜索、编辑、删除、清空、提炼 |
| 关系 | 5 级关系、直接互动/群内见过、设置唯一依赖对象、清除记录 |
| 模型切换 | Ollama 对话模型列表、生成参数 |
| 调试 | 每次模型调用的完整输入/输出/参数/错误/trace |
| 插件管理 | 占位，等插件规范 |
| QQ 账号 | NapCat WebUI、Token、容器重启 |
| API 接入 | 服务商预设、模型、推理强度、图片、上下文、连通测试、用量 |

## 4. 运行约束

- **UI 不写业务逻辑**：PowerShell、HTTP、SQLite、模型调用全部经 `services.py`
- **阻塞调用必须 `run_async`**：否则界面卡死
- **管理员权限**：启停 Windows 服务需要管理员；打包版 manifest 申请 UAC，源码运行会自动提权重启
- **控制台不是独立工具**：exe 向上查找含 `config/app_config.yaml` 的项目根；分发要带整个项目或保证 exe 在项目 `dist/` 下
- **不要用 onefile**：PySide6 6.8 的 Qt 插件在 onefile 下可能起不来；使用 onedir

## 5. 后台任务

```python
from desktop.worker import run_async

run_async(self, services.some_blocking_fn, self._on_result, arg1, arg2)
```

- 线程池执行，完成后通过 Qt 信号回主线程
- 页面销毁时 worker 自动清理
- 不要在 UI 线程直接 `requests` / `subprocess` / `sqlite3` 长查询

## 6. 服务层接口（摘要）

```python
services.start_all_services()
services.stop_all_services()
services.collect_status()
services.list_ollama_models()
services.apply_active({"provider": "ollama", "model": "..."})
services.switch_to_api()
services.list_relationships()
services.list_episodic_memories(...)
services.list_semantic_facts(...)
services.list_llm_traces(limit=100)
services.read_llm_trace(trace_id)
services.clear_llm_traces()
```

完整函数见 `desktop/services.py` 顶部注释与页面调用点。

## 7. 打包

```powershell
.venv\Scripts\python.exe -m PyInstaller desktop\ShelwellConsole.spec --noconfirm
```

产物：

```text
dist\ShelwellConsole\ShelwellConsole.exe
dist_preview\ShelwellConsole\ShelwellConsole.exe   # 预览构建
```

- `ShelwellConsole.spec` 使用 onedir
- `runtime_hook_ssl.py` 修复打包后的证书路径
- 构建前确认 `desktop/pages/` 新页面已加入 `main.py` 的 `PAGES` 和 `stack`
- 控制台运行中不要覆盖 `dist/ShelwellConsole`；先关控制台或构建到 `dist_preview`

## 8. 新增页面

1. 在 `desktop/pages/` 新建 `xxx_page.py`
2. 页面只调 `services.py`，不直接 import backend 实现
3. 在 `main.py`：
   - import 页面
   - `PAGES` 加名称
   - `MainWindow.__init__` 实例化
   - `stack` 元组加页面
   - `_on_nav` 加刷新逻辑（如需要）
4. 更新本文件页面表与 [../文件树.md](../文件树.md)
5. 重新打包验证

## 9. 调试页

- 数据来自 `logs/llm_trace/index.jsonl` 和每次调用的 JSON
- 列表字段：时间、类型、会话、模型、输入字数、耗时、状态
- 详情显示逐条 messages、RAW OUTPUT、CLEANED/BUBBLES、ERROR
- “打开日志目录”“清空 trace”在页面顶部
- 关闭追踪：`config/app_config.yaml` 的 `debug.trace_enabled: false`

## 10. 测试

控制台没有自动化 UI 测试；改页面后至少：

```powershell
.venv\Scripts\python.exe -m py_compile desktop\main.py desktop\services.py desktop\pages\*.py
```

并用源码运行一次，确认页面加载、服务状态、无阻塞。
