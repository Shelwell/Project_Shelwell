# desktop/pages/relationship_page.py
# 职责：关系等级页——查看发言人等级、设置唯一“依赖”对象、重置单条记录

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
)

from desktop import services
from desktop.worker import run_async

LEVEL_COLORS = {
    "陌生": "#888888",
    "认识": "#2a8a5a",
    "了解": "#2a6fb0",
    "信任": "#c07a00",
    "依赖": "#c03030",
}

HEADERS = ["昵称", "QQ", "等级", "消息数", "认识时长", "最后互动"]


class RelationshipPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # ---------- 规则说明 ----------
        rule_box = QGroupBox("判定规则（代码计算，模型不参与）")
        rv = QVBoxLayout(rule_box)
        self.lab_rules = QLabel("加载中…")
        self.lab_rules.setWordWrap(True)
        self.lab_rules.setStyleSheet("color:#444;font-size:12px;")
        rv.addWidget(self.lab_rules)
        layout.addWidget(rule_box)

        # ---------- 依赖对象 ----------
        dep_box = QGroupBox("依赖对象（全局唯一，只可设 1 人）")
        dh = QHBoxLayout(dep_box)
        self.lab_dep = QLabel("未设置")
        self.lab_dep.setStyleSheet("color:#c03030;font-weight:bold;")
        dh.addWidget(self.lab_dep)
        dh.addStretch()
        self.btn_set = QPushButton("将选中行设为依赖")
        self.btn_set.clicked.connect(self.set_dependent)
        dh.addWidget(self.btn_set)
        self.btn_unset = QPushButton("取消依赖")
        self.btn_unset.clicked.connect(self.unset_dependent)
        dh.addWidget(self.btn_unset)
        layout.addWidget(dep_box)

        # ---------- 列表 ----------
        tb = QGroupBox("发言人")
        tv = QVBoxLayout(tb)
        row = QHBoxLayout()
        self.lab_count = QLabel("")
        self.lab_count.setStyleSheet("color:#888;font-size:12px;")
        row.addWidget(self.lab_count)
        row.addStretch()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.reload)
        row.addWidget(btn_refresh)
        self.btn_del = QPushButton("清除选中记录")
        self.btn_del.clicked.connect(self.delete_selected)
        row.addWidget(self.btn_del)
        tv.addLayout(row)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        tv.addWidget(self.table, 1)
        layout.addWidget(tb, 1)

        self.reload()

    # ---------- 数据 ----------

    def reload(self):
        run_async(self, services.list_relationships, self._on_loaded)

    def _on_loaded(self, r):
        self.table.setRowCount(0)
        if not r or not r.get("ok"):
            self.lab_rules.setText("读取失败: %s" % ((r or {}).get("error") or "未知错误"))
            return

        self.lab_rules.setText(self._rules_text(r.get("thresholds") or {}))
        dep_id = str(r.get("dependent_id") or "")
        items = r.get("items") or []
        dep_name = ""
        for it in items:
            if str(it.get("speaker_id")) == dep_id and dep_id:
                dep_name = it.get("display_name") or dep_id
        self.lab_dep.setText(
            "当前依赖对象：%s（%s）" % (dep_name, dep_id) if dep_id else "当前未设置")

        self.lab_count.setText("共 %d 位发言人" % len(items))
        for it in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                it.get("display_name") or "（未命名）",
                str(it.get("speaker_id") or ""),
                it.get("level_cn") or "",
                str(it.get("msg_count", 0)),
                it.get("hours_text") or "",
                self._fmt_time(it.get("last_seen") or ""),
            ]
            for col, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if col in (1, 3, 4):
                    cell.setTextAlignment(Qt.AlignCenter)
                if col == 2:
                    cell.setToolTip(it.get("attitude") or "")
                    color = LEVEL_COLORS.get(text)
                    if color:
                        cell.setForeground(QColor(color))
                self.table.setItem(row, col, cell)
        self.table.resizeColumnsToContents()

    @staticmethod
    def _fmt_time(iso: str) -> str:
        if not iso:
            return ""
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%m-%d %H:%M")
        except Exception:
            return iso[:16]

    @staticmethod
    def _rules_text(t: dict) -> str:
        def day(h):
            try:
                return int(float(h) // 24)
            except Exception:
                return 0

        def val(k, default=0):
            try:
                return int(float(t.get(k, default)))
            except Exception:
                return default

        return (
            "陌生：0-%d 条 或 0-%d 小时（%d 天）　"
            "认识：%d-%d 条 或 %d-%d 小时（%d 天）\n"
            "了解：%d-%d 条 或 %d-%d 小时（%d 天）　"
            "信任：>%d 条 且 >%d 小时（%d 天）\n"
            "依赖：>%d 条 且 >%d 小时（%d 天），且被设为依赖对象（全局限 1 人）\n"
            "傲娇/毒舌/玩梗：私聊“信任/依赖”，群聊仅“依赖”。等级由代码按归档消息计算，模型无权升降。"
            % (val("stranger_max_msgs"), val("stranger_max_hours"),
               day(val("stranger_max_hours")),
               val("stranger_max_msgs") + 1, val("acquaint_max_msgs"),
               val("stranger_max_hours") + 1, val("acquaint_max_hours"),
               day(val("acquaint_max_hours")),
               val("acquaint_max_msgs") + 1, val("known_max_msgs"),
               val("acquaint_max_hours") + 1, val("known_max_hours"),
               day(val("known_max_hours")),
               val("trust_min_msgs"), val("trust_min_hours"),
               day(val("trust_min_hours")),
               val("depend_min_msgs"), val("depend_min_hours"),
               day(val("depend_min_hours")))
        )

    # ---------- 操作 ----------

    def _selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "先在列表里选中一位发言人")
            return None, None
        name = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
        sid = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        return sid, name

    def set_dependent(self):
        sid, name = self._selected()
        if not sid:
            return
        r = QMessageBox.question(
            self, "确认", "将 %s（%s）设为唯一的依赖对象？\n"
            "设置后只有此人可能在满足条件时达到“依赖”。" % (name, sid),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.btn_set.setEnabled(False)
        run_async(self, services.set_relationship_dependent,
                  self._on_saved, sid, name)

    def unset_dependent(self):
        self.btn_unset.setEnabled(False)
        run_async(self, services.set_relationship_dependent,
                  self._on_saved, "", "")

    def delete_selected(self):
        sid, name = self._selected()
        if not sid:
            return
        r = QMessageBox.question(
            self, "确认", "清除 %s（%s）的关系记录？\n"
            "消息计数与认识时间会归零，下次说话从“陌生”重新累计。" % (name, sid),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.btn_del.setEnabled(False)
        run_async(self, services.clear_relationship, self._on_deleted, sid)

    def _on_saved(self, r):
        self.btn_set.setEnabled(True)
        self.btn_unset.setEnabled(True)
        msg = (r or {}).get("message", "")
        if r and r.get("ok"):
            self.reload()
        else:
            QMessageBox.warning(self, "保存失败", msg or "未知错误")

    def _on_deleted(self, r):
        self.btn_del.setEnabled(True)
        if r and r.get("ok"):
            self.reload()
        else:
            QMessageBox.warning(self, "清除失败", (r or {}).get("message", "未知错误"))
