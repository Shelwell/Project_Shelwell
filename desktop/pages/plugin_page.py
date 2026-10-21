# desktop/pages/plugin_page.py
# 职责：插件管理页——当前为占位，规范待定

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit

from desktop.services import ROOT


class PluginPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        tip = QLabel("插件系统规范待定：先研究 deepseek-harness 的装配方式，产出 docs/plugin-spec.md 后实现。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666;")
        layout.addWidget(tip)

        layout.addWidget(QLabel("plugins/ 目录扫描："))
        txt = QTextEdit()
        txt.setReadOnly(True)
        pdir = ROOT / "plugins"
        if pdir.exists():
            names = [p.name for p in pdir.iterdir() if p.is_dir()]
            txt.setPlainText("\n".join(names) if names else "（无插件）")
        else:
            txt.setPlainText("plugins/ 目录尚未创建")
        layout.addWidget(txt)
        layout.addStretch()