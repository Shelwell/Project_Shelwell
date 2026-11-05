# desktop/main.py
# 职责：Shelwell 桌面控制台（PySide6）
# 入口：python desktop/main.py

import ctypes
import sys
from pathlib import Path

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
from desktop.pages.relationship_page import RelationshipPage
from desktop.pages.trace_page import TracePage


# ---------------- 管理员权限 ----------------
# 启停 Windows 服务需要管理员权限；打包版通过 manifest（uac_admin）在启动时请求，
# 源码运行则由下面的 relaunch 逻辑自行提权重启。

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重启自己；成功返回 True（调用方应退出当前进程）"""
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
            workdir = str(Path(sys.executable).resolve().parent)
        else:
            # 源码运行：用 -m 以包方式启动，工作目录必须是项目根
            exe = sys.executable
            params = "-m desktop.main " + " ".join(
                f'"{a}"' for a in sys.argv[1:])
            workdir = str(ROOT)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, workdir, 1)
        return rc > 32   # >32 表示成功启动
    except Exception as e:
        print(f"[Console] 提权失败: {e}")
        return False


ADMIN = is_admin()

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

        # 权限状态：启停服务必须管理员
        self.lab_admin = QLabel()
        self.lab_admin.setAlignment(Qt.AlignCenter)
        if ADMIN:
            self.lab_admin.setText("运行权限：管理员 ✓")
            self.lab_admin.setStyleSheet("color:#2a8;font-size:12px;")
        else:
            self.lab_admin.setText("运行权限：普通用户 —— 启动/停止服务需要管理员权限")
            self.lab_admin.setStyleSheet("color:#c60;font-size:12px;")
        layout.addWidget(self.lab_admin)

        if not ADMIN:
            row = QHBoxLayout()
            btn_elev = QPushButton("以管理员身份重启")
            btn_elev.clicked.connect(self.relaunch_admin)
            row.addStretch()
            row.addWidget(btn_elev)
            row.addStretch()
            layout.addLayout(row)

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

    def _need_admin(self) -> bool:
        """需要管理员但当前不是 → 提示并以管理员重启"""
        if ADMIN:
            return False
        r = QMessageBox.question(
            self, "需要管理员权限",
            "启动/停止 Windows 服务需要管理员权限。\n\n是否以管理员身份重启控制台？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r == QMessageBox.Yes:
            self.relaunch_admin()
        return True

    def relaunch_admin(self):
        if relaunch_as_admin():
            QApplication.instance().quit()
        else:
            QMessageBox.warning(
                self, "提权失败",
                "没能以管理员身份启动（可能取消了 UAC）。\n"
                "可以右键“以管理员身份运行”本程序，或从源码运行时用管理员终端。")

    def start_all(self):
        if self._need_admin():
            return
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
        if self._need_admin():
            return
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
        if self._need_admin():
            return
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

PAGES = ["首页", "人格管理", "记忆管理", "关系", "模型切换", "调试",
         "插件管理", "QQ 账号", "API 接入"]


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
        self.relationship_page = RelationshipPage()
        self.model_page = ModelPage()
        self.trace_page = TracePage()
        self.plugin_page = PluginPage()
        self.qq_page = QQPage()
        self.api_page = ApiPage()
        for w in (self.home_page, self.persona_page, self.memory_page,
                  self.relationship_page, self.model_page, self.trace_page,
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
        elif PAGES[idx] == "关系":
            self.relationship_page.reload()
        elif PAGES[idx] == "调试":
            self.trace_page.reload()


def main():
    # 启停 Windows 服务需要管理员权限：
    #   打包版由 exe manifest（uac_admin）在启动时请求 UAC；
    #   源码运行则在这里自动提权重启，避免“停止全部服务”静默失败。
    if not ADMIN and relaunch_as_admin():
        return
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()