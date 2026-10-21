# Shelwell 桌面控制台

PySide6 桌面应用，替代 Web 版控制台（`app/console.py`），零端口，可打包为 exe。

## 运行（源码）
E:\Shelwell.venv\Scripts\python.exe desktop\main.pytext复制下载
## 打包
cd E:\Shelwell.venv\Scripts\python.exe -m PyInstaller desktop\ShelwellConsole.spec --noconfirm --cleantext复制下载
产物：`dist/ShelwellConsole/ShelwellConsole.exe`（onedir 模式，约 104MB）。

**不要用 `--onefile`**：PySide6 6.8 的 Qt 插件在 onefile 模式下有部署缺陷，窗口可能起不来。

## 分发说明

exe **依赖项目目录存在**。它启动后向上查找含 `config/app_config.yaml` 的目录作为项目根，找不到则功能异常。

因此分发方式是：**整个 `E:\Shelwell` 项目目录一起带走**，或至少保证 exe 放在项目根的 `dist/` 下。exe 不是独立工具，它管理的是本机上的 Ollama / NapCat / Windows 服务。

## 目录结构
desktop/├── main.py              主窗口 + 服务状态页├── worker.py            后台任务（线程池 + 信号）├── services.py          业务逻辑层（无 UI 依赖）├── ShelwellConsole.spec 打包配置└── pages/├── persona_page.py  人格管理├── model_page.py    模型切换├── api_page.py      API 接入├── qq_page.py       QQ 账号└── plugin_page.py   插件管理（占位）text复制下载
## 设计约束

- UI 层不写业务逻辑，全部经 `services.py`
- 所有阻塞调用（PowerShell、HTTP）必须走 `run_async`，否则界面卡死
- 新增页面：在 `pages/` 下建模块，`main.py` 的 `PAGES` 和堆栈同步加