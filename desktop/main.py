# desktop/main.py
# 职责：Shelwell 桌面控制台（PySide6）
# 入口：python desktop/main.py

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QListWidget, QListWidgetItem,
    QStackedWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGridLayout, QGroupBox, QTextEdit, QComboBox, QFrame, QMessageBox,
)

from desktop.services import ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop import services
from desktop.worker import run_async
from desktop.pages.persona_page import PersonaPage
from desktop.pages.model_page import ModelPage
from desktop.pages.api_page import ApiPage
from desktop.pages.qq_page import QQPage
from desktop.pages.plugin_page import PluginPage
from desktop.pages.memory_page import MemoryPage

# ---------------- 首页：启动 + 服务状态 ----------------

class HomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        title = QLabel("Shelwell")
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("一键启动 / 状态总览")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #888;")
        layout.addWidget(sub)

        # 启动按钮
        start_row = QHBoxLayout()
        start_row.addStretch()
        self.btn_start_all = QPushButton("一键启动全部服务")
        self.btn_start_all.setMinimumHeight(44)
        self.btn_start_all.setStyleSheet(
            "QPushButton{background:#2d7d46;color:white;font-size:15px;"
            "font-weight:bold;border-radius:6px;padding:0 24px;}"
            "QPushButton:disabled{background:#999;}")
        self.btn_start_all.clicked.connect(self.start_all)
        start_row.addWidget(self.btn_start_all)

        self.btn_stop_all = QPushButton("停止全部服务")
        self.btn_stop_all.setMinimumHeight(44)
        self.btn_stop_all.setStyleSheet(
            "QPushButton{background:#b33;color:white;font-size:15px;"
            "font-weight:bold;border-radius:6px;padding:0 24px;}"
            "QPushButton:disabled{background:#999;}")
        self.btn_stop_all.clicked.connect(self.stop_all)
        start_row.addWidget(self.btn_stop_all)

        start_row.addStretch()
        layout.addLayout(start_row)

        # 状态卡片（标题行含刷新按钮）
        self.box = QGroupBox("服务状态")
        box_layout = QVBoxLayout(self.box)
        head_row = QHBoxLayout()
        self.lab_auto = QLabel("自动刷新中")
        self.lab_auto.setStyleSheet("color: #888; font-size: 12px;")
        head_row.addWidget(self.lab_auto)
        head_row.addStretch()
        self.btn_refresh = QPushButton("刷新状态")
        self.btn_refresh.clicked.connect(self.refresh)
        head_row.addWidget(self.btn_refresh)
        box_layout.addLayout(head_row)
        grid = QGridLayout()
        self.labels = {}
        items = [
            ("ShelwellOllama", "Ollama 服务"),
            ("ShelwellQQBot", "QQBot 服务"),
            ("napcat", "NapCat 容器"),
            ("port_11434", "端口 11434"),
            ("port_8765", "端口 8765"),
        ]
        for i, (key, text) in enumerate(items):
            r, c = divmod(i, 2)
            grid.addWidget(QLabel(text), r, c * 2)
            lab = QLabel("—")
            lab.setStyleSheet("font-weight: bold;")
            grid.addWidget(lab, r, c * 2 + 1)
            self.labels[key] = lab
        box_layout.addLayout(grid)
        layout.addWidget(self.box)

        # 快捷操作
        btn_row = QHBoxLayout()
        self.btn_restart_bot = QPushButton("重启 QQBot")
        self.btn_restart_bot.clicked.connect(lambda: self._restart("ShelwellQQBot"))
        self.btn_restart_napcat = QPushButton("重启 NapCat")
        self.btn_restart_napcat.clicked.connect(self._restart_napcat)
        btn_row.addWidget(self.btn_restart_bot)
        btn_row.addWidget(self.btn_restart_napcat)
        self.btn_unload = QPushButton("卸载模型")
        self.btn_unload.setToolTip("卸载 Ollama 生成模型，腾出显存给其它程序")
        self.btn_unload.clicked.connect(self.unload_models)
        btn_row.addWidget(self.btn_unload)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 日志
        log_box = QGroupBox("日志")
        log_layout = QVBoxLayout(log_box)
        sel_row = QHBoxLayout()
        self.log_select = QComboBox()
        sel_row.addWidget(QLabel("文件"))
        sel_row.addWidget(self.log_select, 1)
        btn_log = QPushButton("查看")
        btn_log.clicked.connect(self.load_log)
        sel_row.addWidget(btn_log)
        log_layout.addLayout(sel_row)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_box, 1)

        self._refreshing = False
        self.refresh()
        # 每 2 秒自动刷新
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def start_all(self):
        r = QMessageBox.question(
            self, "确认", "确定启动全部 Shelwell 服务？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.btn_start_all.setEnabled(False)
        self.btn_stop_all.setEnabled(False)
        self.btn_start_all.setText("启动中，请稍候…")
        run_async(self, services.start_all_services, self._on_started)

    def _on_started(self, log):
        self.btn_start_all.setEnabled(True)
        self.btn_stop_all.setEnabled(True)
        self.btn_start_all.setText("一键启动全部服务")
        self.log_view.setPlainText(str(log))
        self.refresh()

    def stop_all(self):
        r = QMessageBox.question(
            self, "确认", "确定停止全部 Shelwell 服务？\nQQ 对话将中断。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.btn_start_all.setEnabled(False)
        self.btn_stop_all.setEnabled(False)
        self.btn_stop_all.setText("停止中，请稍候…")
        run_async(self, services.stop_all_services, self._on_stopped)

    def _on_stopped(self, log):
        self.btn_start_all.setEnabled(True)
        self.btn_stop_all.setEnabled(True)
        self.btn_stop_all.setText("停止全部服务")
        self.log_view.setPlainText(str(log))
        self.refresh()

    def refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        self.btn_refresh.setEnabled(False)
        run_async(self, services.collect_status, self._on_status,
                  on_error=self._on_status_error)

    def _on_status_error(self, err):
        self._refreshing = False
        self.btn_refresh.setEnabled(True)
        print("[Home] 状态刷新失败:", err)

    def _on_status(self, data):
        for k, lab in self.labels.items():
            v = data.get(k)
            if isinstance(v, bool):
                lab.setText("OPEN" if v else "CLOSED")
                ok = v
            else:
                v = str(v)
                lab.setText(v)
                ok = v.upper() in ("RUNNING", "OPEN")
            lab.setStyleSheet(
                "color: green; font-weight: bold;" if ok
                else "color: #b00; font-weight: bold;")
        self._refreshing = False
        self.btn_refresh.setEnabled(True)
        if self.log_select.count() == 0:
            run_async(self, services.list_logs, self._on_logs)

    def _on_logs(self, logs):
        self.log_select.clear()
        self.log_select.addItems(logs)

    def load_log(self):
        name = self.log_select.currentText()
        if name:
            run_async(self, services.read_log, self.log_view.setPlainText, name, 150)

    def _restart(self, name):
        run_async(self, services.restart_service, lambda _: self.refresh(), name)

    def _restart_napcat(self):
        run_async(self, services.restart_napcat, lambda _: self.refresh())

    def unload_models(self):
        self.btn_unload.setEnabled(False)
        self.btn_unload.setText("卸载中…")
        run_async(self, services.unload_models, self._on_unloaded)

    def _on_unloaded(self, msg):
        self.btn_unload.setEnabled(True)
        self.btn_unload.setText("卸载模型")
        QMessageBox.information(self, "卸载模型", str(msg))
        self.refresh()


# ---------------- 主窗口 ----------------

PAGES = ["首页", "人格管理", "记忆管理", "模型切换", "插件管理", "QQ 账号", "API 接入"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Shelwell Console")
        self.resize(1100, 720)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.nav = QListWidget()
        self.nav.setFixedWidth(160)
        self.nav.setFont(QFont("Microsoft YaHei", 10))
        for p in PAGES:
            self.nav.addItem(QListWidgetItem(p))
        self.nav.currentRowChanged.connect(self._on_nav)
        layout.addWidget(self.nav)

        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setStyleSheet("color: #ddd;")
        layout.addWidget(line)

        self.stack = QStackedWidget()
        self.home_page = HomePage()
        self.persona_page = PersonaPage()
        self.memory_page = MemoryPage()
        self.model_page = ModelPage()
        self.plugin_page = PluginPage()
        self.qq_page = QQPage()
        self.api_page = ApiPage()
        for w in (self.home_page, self.persona_page, self.memory_page, self.model_page,
                  self.plugin_page, self.qq_page, self.api_page):
            self.stack.addWidget(w)
        layout.addWidget(self.stack, 1)

        self.nav.setCurrentRow(0)

    def _on_nav(self, idx):
        if idx < 0:
            return
        self.stack.setCurrentIndex(idx)
        if PAGES[idx] == "首页":
            self.home_page.refresh()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()