# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec - Shelwell 桌面控制台
# 打包：pyinstaller desktop/ShelwellConsole.spec --noconfirm
# 产物：dist/ShelwellConsole/

import sys
from pathlib import Path

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
    "tkinter", "matplotlib", "numpy", "pandas", "scipy",
    "streamlit", "chromadb", "torch", "transformers",
    "IPython", "jupyter", "notebook", "pytest",
]

a = Analysis(
    [str(ROOT / "desktop" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[
        (r"E:\Anaconda3\Library\bin\libssl-3-x64.dll", "."),
        (r"E:\Anaconda3\Library\bin\libcrypto-3-x64.dll", "."),
    ],
    datas=[
        (r"E:\Shelwell\.venv\Lib\site-packages\certifi\cacert.pem", "."),
    ],
    hiddenimports=[
        "backend.config_loader",
        "backend.runtime_config",
        "desktop.pages.persona_page",
        "desktop.pages.model_page",
        "desktop.pages.api_page",
        "desktop.pages.qq_page",
        "desktop.pages.plugin_page",
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