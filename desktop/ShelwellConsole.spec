# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec - Shelwell 桌面控制台
# 打包：pyinstaller desktop/ShelwellConsole.spec --noconfirm
# 产物：dist/ShelwellConsole/

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(r"E:\Shelwell")

# 裁剪：这些 Qt 模块用不到，排除以减小体积
EXCLUDE_QT = [
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
]

# 裁剪：无关的 Python 包
EXCLUDES = EXCLUDE_QT + [
    "tkinter", "matplotlib", "pandas", "scipy",
    "streamlit", "torch", "transformers",
    "IPython", "jupyter", "notebook", "pytest",
]

chroma_datas, chroma_binaries, chroma_hidden = collect_all("chromadb")

a = Analysis(
    [str(ROOT / "desktop" / "main.py")],
    pathex=[str(ROOT)],
    binaries=chroma_binaries + [
        (r"E:\Anaconda3\Library\bin\libssl-3-x64.dll", "."),
        (r"E:\Anaconda3\Library\bin\libcrypto-3-x64.dll", "."),
        (r"E:\Anaconda3\DLLs\_sqlite3.pyd", "."),
        (r"E:\Anaconda3\Library\bin\sqlite3.dll", "."),
        (r"E:\Anaconda3\Library\bin\ffi-7.dll", "."),
        (r"E:\Anaconda3\Library\bin\ffi-8.dll", "."),
        (r"E:\Anaconda3\Library\bin\ffi.dll", "."),
    ],
    datas=chroma_datas + [
        (r"E:\Shelwell\.venv\Lib\site-packages\certifi\cacert.pem", "."),
    ],
    hiddenimports=chroma_hidden + [
        "backend.config_loader",
        "backend.runtime_config",
        "desktop.pages.persona_page",
        "desktop.pages.model_page",
        "desktop.pages.api_page",
        "desktop.pages.qq_page",
        "desktop.pages.plugin_page",
        "desktop.pages.memory_page",
        "sqlite3", "_sqlite3",
        "chromadb",
        "backend.memory",
        "backend.memory.models",
        "backend.memory.archive",
        "backend.memory.indexer",
        "backend.memory.embedder",
        "backend.memory.jobs",
        "backend.memory.service",
        "backend.memory_retriever",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "desktop" / "runtime_hook_ssl.py")],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ShelwellConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    # 启停 Windows 服务需要管理员权限：启动时请求 UAC（右键运行也可）
    uac_admin=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ShelwellConsole",
)