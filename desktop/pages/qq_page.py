# desktop/pages/qq_page.py
# 职责：QQ 账号页——WebUI 链接、Token 提取、容器重启

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTextEdit,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from desktop import services
from desktop.worker import run_async


class QQPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        box = QGroupBox("NapCat")
        v = QVBoxLayout(box)
        v.addWidget(QLabel("账号由 NapCat 管理。掉线需在 WebUI 重新扫码，无法自动恢复。"))

        row = QHBoxLayout()
        btn_web = QPushButton("打开 WebUI (6099)")
        btn_web.clicked.connect(self.open_webui)
        row.addWidget(btn_web)
        btn_token = QPushButton("提取 Token")
        btn_token.clicked.connect(self.load_token)
        row.addWidget(btn_token)
        btn_restart = QPushButton("重启容器（需重新扫码）")
        btn_restart.clicked.connect(self.restart)
        row.addWidget(btn_restart)
        btn_copy = QPushButton("复制登录地址")
        btn_copy.clicked.connect(self.copy_url)
        row.addWidget(btn_copy)
        row.addStretch()
        v.addLayout(row)

        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setFixedHeight(120)
        v.addWidget(self.txt)
        layout.addWidget(box)
        layout.addStretch()

    def open_webui(self):
        QDesktopServices.openUrl(QUrl("http://127.0.0.1:6099/webui"))

    def load_token(self):
        run_async(self, services.get_napcat_token, self._on_token)

    def _on_token(self, token):
        if token:
            url = f"http://127.0.0.1:6099/webui?token={token}"
            self.txt.setPlainText(f"Token: {token}\n\n登录地址:\n{url}")
        else:
            self.txt.setPlainText("未找到 Token。请在下方按钮打开 WebUI 后用默认 token 登录，或查看容器日志。")

    def restart(self):
        run_async(self, services.restart_napcat, lambda o: self.txt.setPlainText(o))

    def copy_url(self):
        from PySide6.QtWidgets import QApplication
        text = self.txt.toPlainText()
        if "http" in text:
            url = text[text.index("http"):].strip()
            QApplication.clipboard().setText(url)
            self.txt.append("\n已复制到剪贴板")
