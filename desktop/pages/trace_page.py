# desktop/pages/trace_page.py
# 职责：模型调试页——查看每次对话实际送入模型的 messages、原始输出与错误

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QPlainTextEdit, QAbstractItemView, QSplitter,
)

from desktop import services
from desktop.worker import run_async

HEADERS = ["时间", "类型", "会话", "模型", "输入", "耗时", "状态"]


class TracePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        box = QGroupBox("模型调用")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        self.lab_info = QLabel("加载中…")
        self.lab_info.setStyleSheet("color:#888;font-size:12px;")
        row.addWidget(self.lab_info)
        row.addStretch()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.reload)
        row.addWidget(btn_refresh)
        btn_dir = QPushButton("打开日志目录")
        btn_dir.clicked.connect(self.open_dir)
        row.addWidget(btn_dir)
        btn_clear = QPushButton("清空 trace")
        btn_clear.clicked.connect(self.clear_traces)
        row.addWidget(btn_clear)
        v.addLayout(row)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.currentCellChanged.connect(lambda *_: self.load_selected())
        split.addWidget(self.table)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Microsoft YaHei", 9))
        self.detail.setPlaceholderText("选择上方一条调用，查看完整输入/输出")
        split.addWidget(self.detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        v.addWidget(split, 1)
        layout.addWidget(box, 1)

        self._items = []
        self._loading = False
        self.reload()

    # ---------- 列表 ----------

    def reload(self):
        self.lab_info.setText("加载中…")
        run_async(self, services.list_llm_traces, self._on_listed, 200)

    def _on_listed(self, r):
        r = r or {}
        self.table.setRowCount(0)
        self._items = r.get("items") or []
        if not r.get("ok"):
            self.lab_info.setText("读取失败: %s" % (r.get("error") or "未知错误"))
            return
        self.lab_info.setText("共 %d 次调用　目录: %s"
                             % (len(self._items), r.get("trace_dir", "")))
        for it in self._items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            ts = str(it.get("ts", ""))[:19].replace("T", " ")
            ok = "OK" if it.get("ok") else "ERR"
            values = [ts, it.get("kind", ""), it.get("session_id", ""),
                      it.get("model", ""), str(it.get("prompt_chars", "")),
                      "%sms" % it.get("duration_ms", ""), ok]
            for col, text in enumerate(values):
                cell = QTableWidgetItem(str(text))
                if col >= 4:
                    cell.setTextAlignment(Qt.AlignCenter)
                if col == 6 and not it.get("ok"):
                    cell.setForeground(Qt.red)
                cell.setData(Qt.UserRole, it.get("trace_id", ""))
                self.table.setItem(row, col, cell)
        self.table.resizeColumnsToContents()
        if self._items:
            self.table.selectRow(0)
        else:
            self.detail.clear()

    # ---------- 详情 ----------

    def load_selected(self):
        if self._loading:
            return
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if not item:
            return
        tid = item.data(Qt.UserRole)
        if not tid:
            return
        self._loading = True
        self.detail.setPlainText("读取中…")
        run_async(self, services.read_llm_trace, self._on_detail, tid)

    def _on_detail(self, r):
        self._loading = False
        r = r or {}
        if not r.get("ok"):
            self.detail.setPlainText("读取失败: %s" % (r.get("error") or "未知错误"))
            return
        self.detail.setPlainText(self._format(r.get("data") or {}))
        self.detail.moveCursor(QTextCursor.Start)

    @staticmethod
    def _format(d: dict) -> str:
        lines = []
        lines.append("时间: %s" % d.get("ts", ""))
        lines.append("模型: %s | provider: %s" % (d.get("model", ""), d.get("provider", "")))
        lines.append("参数: %s" % (d.get("params") or {}))
        meta = d.get("meta") or {}
        lines.append("会话: %s | 说话人: %s" % (meta.get("session_id", ""), meta.get("speaker", "")))
        if meta.get("query"):
            lines.append("本轮输入: %s" % meta.get("query"))
        if meta.get("build_error"):
            lines.append("上下文构建错误: %s" % meta.get("build_error"))
        p = d.get("prompt") or {}
        lines.append("输入: %s 条消息 / %s 字 | system %s 字"
                     % (p.get("n_messages", ""), p.get("total_chars", ""),
                        p.get("system_chars", "")))
        lines.append("耗时: %s ms | 状态: %s"
                     % (d.get("duration_ms", ""), "OK" if d.get("ok") else "ERROR"))
        if d.get("usage"):
            lines.append("用量: %s" % (d.get("usage") or {}))

        for i, m in enumerate(d.get("messages") or []):
            if not isinstance(m, dict):
                lines.append("\n===== [%d] %s =====" % (i, m))
                continue
            content = str(m.get("content", ""))
            lines.append("\n===== [%d] %s (%d 字) =====" % (
                i, m.get("role", "?"), len(content)))
            lines.append(content)

        lines.append("\n===== RAW OUTPUT =====")
        lines.append(str(d.get("raw_reply", "")))
        lines.append("\n===== CLEANED / BUBBLES =====")
        lines.append(str(d.get("cleaned_reply", "")))
        lines.append("bubbles: %s" % (d.get("bubbles") or []))
        lines.append("silent: %s" % d.get("silent"))
        if d.get("error"):
            lines.append("\n===== ERROR =====")
            lines.append(str(d.get("error", "")))
            if d.get("error_detail"):
                lines.append("\n===== ERROR DETAIL =====")
                lines.append(str(d.get("error_detail", "")))
        return "\n".join(lines)

    # ---------- 操作 ----------

    def open_dir(self):
        msg = services.open_trace_dir()
        self.lab_info.setText(str(msg))

    def clear_traces(self):
        if QMessageBox.question(
                self, "确认", "清空全部模型 trace？不会影响记忆和配置。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        msg = services.clear_llm_traces()
        QMessageBox.information(self, "清空 trace", str(msg))
        self.reload()
