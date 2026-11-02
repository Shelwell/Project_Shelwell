# desktop/pages/persona_page.py
# 职责：人格管理页——切换 / 新建 / 删除 / 模板示例

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QComboBox, QFormLayout, QMessageBox, QLineEdit,
    QTextEdit, QScrollArea, QFrame,
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont

from desktop import services
from desktop.worker import run_async


class PersonaPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)

        # ---------- 当前 ----------
        box = QGroupBox("当前生效")
        form = QFormLayout(box)
        self.lab_persona = QLabel("—")
        self.lab_facts = QLabel("—")
        self.lab_lora = QLabel("—")
        self.lab_provider = QLabel("—")
        form.addRow("人格", self.lab_persona)
        form.addRow("事实清单", self.lab_facts)
        form.addRow("LoRA", self.lab_lora)
        form.addRow("推理后端", self.lab_provider)
        layout.addWidget(box)

        # ---------- 切换 ----------
        switch = QGroupBox("切换")
        sl = QVBoxLayout(switch)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("人格"))
        self.cb_persona = QComboBox()
        row1.addWidget(self.cb_persona, 1)
        row1.addWidget(QLabel("事实清单"))
        self.cb_facts = QComboBox()
        row1.addWidget(self.cb_facts, 1)
        sl.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("LoRA"))
        self.cb_lora = QComboBox()
        row2.addWidget(self.cb_lora, 1)
        row2.addStretch()
        btn_apply = QPushButton("应用")
        btn_apply.clicked.connect(self.apply)
        row2.addWidget(btn_apply)
        sl.addLayout(row2)
        layout.addWidget(switch)

        # ---------- 新建 ----------
        create = QGroupBox("新建人格")
        cl = QVBoxLayout(create)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("名称"))
        self.ed_new = QLineEdit()
        self.ed_new.setPlaceholderText("小写字母/数字/下划线，如 my_persona")
        crow.addWidget(self.ed_new, 1)
        btn_create = QPushButton("创建")
        btn_create.clicked.connect(self.create)
        crow.addWidget(btn_create)
        cl.addLayout(crow)
        cl.addWidget(QLabel("创建后会在 config/persona_prompts/ 生成模板文件，编辑后即可使用。"))

        drow = QHBoxLayout()
        drow.addWidget(QLabel("删除"))
        self.cb_del = QComboBox()
        drow.addWidget(self.cb_del, 1)
        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self.delete)
        drow.addWidget(btn_del)
        cl.addLayout(drow)
        layout.addWidget(create)

        # ---------- 记忆管理 ----------
        mem_box = QGroupBox("记忆管理")
        ml = QVBoxLayout(mem_box)
        self.lab_mem = QLabel("记忆条数：加载中…")
        self.lab_mem.setStyleSheet("color: #555;")
        ml.addWidget(self.lab_mem)

        btn_row = QHBoxLayout()
        self.btn_mem_refresh = QPushButton("刷新统计")
        self.btn_mem_refresh.clicked.connect(self.refresh_memory)
        btn_row.addWidget(self.btn_mem_refresh)

        self.btn_clear_mem = QPushButton("清空全部记忆")
        self.btn_clear_mem.setStyleSheet(
            "QPushButton{background:#888;color:white;border-radius:4px;padding:4px 12px;}")
        self.btn_clear_mem.clicked.connect(self.on_clear_memory_clicked)
        btn_row.addWidget(self.btn_clear_mem)
        btn_row.addStretch()
        ml.addLayout(btn_row)

        warn = QLabel("清空将删除全部归档、向量索引和关键词索引，不可恢复。")
        warn.setStyleSheet("color: #b33; font-size: 12px;")
        ml.addWidget(warn)
        layout.addWidget(mem_box)

        self._clear_click_count = 0
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(5000)  # 5 秒无操作重置
        self._clear_timer.timeout.connect(self._reset_clear_button)

        # ---------- 模板示例 ----------
        tpl = QGroupBox("人格模板示例（照着填写）")
        tl = QVBoxLayout(tpl)
        self.tpl_view = QTextEdit()
        self.tpl_view.setReadOnly(True)
        self.tpl_view.setFont(QFont("Consolas", 9))
        self.tpl_view.setMinimumHeight(320)
        self.tpl_view.setPlainText(services.PERSONA_TEMPLATE)
        tl.addWidget(self.tpl_view)
        layout.addWidget(tpl)

        layout.addStretch()
        self.refresh()

    def refresh(self):
        run_async(self, services.get_active, self._on_active)
        run_async(self, services.list_personas, self._fill_personas)
        run_async(self, services.list_facts, self._fill_facts)
        run_async(self, services.list_loras, self._fill_loras)
        self.refresh_memory()

    def refresh_memory(self):
        run_async(self, services.get_memory_stats, self._on_mem_stats)

    def _on_mem_stats(self, st):
        if st.get("error"):
            self.lab_mem.setText("记忆条数：读取失败（%s）" % st["error"][:40])
            return
        self.lab_mem.setText(
            "记忆条数：归档 %d 条 | 向量 %d 条 | 关键词 %d 条 | 会话 %d 个"
            % (st.get("archive", 0), st.get("vector", 0),
               st.get("lexical", 0), st.get("sessions", 0)))

    def on_clear_memory_clicked(self):
        self._clear_click_count += 1
        n = self._clear_click_count
        if n == 1:
            self.btn_clear_mem.setText("清空全部记忆 (1/3) 点击确认")
            self.btn_clear_mem.setStyleSheet(
                "QPushButton{background:#b8860b;color:white;border-radius:4px;padding:4px 12px;}")
            self._clear_timer.start()
        elif n == 2:
            self.btn_clear_mem.setText("确认清空？再点一次 (2/3)")
            self.btn_clear_mem.setStyleSheet(
                "QPushButton{background:#c60;color:white;border-radius:4px;padding:4px 12px;}")
            self._clear_timer.start()
        elif n >= 3:
            self._clear_timer.stop()
            self.btn_clear_mem.setText("正在清空…")
            self.btn_clear_mem.setEnabled(False)
            run_async(self, services.clear_all_memory, self._on_mem_cleared)

    def _reset_clear_button(self):
        self._clear_click_count = 0
        self.btn_clear_mem.setText("清空全部记忆")
        self.btn_clear_mem.setEnabled(True)
        self.btn_clear_mem.setStyleSheet(
            "QPushButton{background:#888;color:white;border-radius:4px;padding:4px 12px;}")

    def _on_mem_cleared(self, log):
        self._reset_clear_button()
        QMessageBox.information(self, "完成", "记忆已清空。\n\n" + str(log))
        self.refresh_memory()

    def _on_active(self, a):
        self.lab_persona.setText(str(a.get("persona")))
        self.lab_facts.setText(str(a.get("facts") or "(无)"))
        self.lab_lora.setText(str(a.get("lora") or "(不挂载)"))
        self.lab_provider.setText(str(a.get("provider")))

    def _fill_personas(self, items):
        cur = self.cb_persona.currentText()
        self.cb_persona.clear()
        self.cb_persona.addItems(items)
        if cur in items:
            self.cb_persona.setCurrentText(cur)
        self.cb_del.clear()
        self.cb_del.addItems(items)

    def _fill_facts(self, items):
        self.cb_facts.clear()
        self.cb_facts.addItem("(不使用)", None)
        for i in items:
            self.cb_facts.addItem(i, i)

    def _fill_loras(self, items):
        self.cb_lora.clear()
        self.cb_lora.addItem("(不挂载)", None)
        for i in items:
            self.cb_lora.addItem(i, i)

    def apply(self):
        patch = {
            "persona": self.cb_persona.currentText(),
            "facts": self.cb_facts.currentData(),
            "lora": self.cb_lora.currentData(),
        }
        run_async(self, services.apply_active, self._on_applied, patch)

    def _on_applied(self, _):
        QMessageBox.information(self, "完成", "已应用，下一轮对话生效")
        self.refresh()

    def create(self):
        name = self.ed_new.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入人格名称")
            return
        run_async(self, services.create_persona, self._on_created, name)

    def _on_created(self, msg):
        if str(msg).startswith("OK:"):
            QMessageBox.information(self, "完成", "已创建模板文件，编辑后即可切换使用。")
            self.ed_new.clear()
            self.refresh()
        else:
            QMessageBox.warning(self, "创建失败", str(msg))

    def delete(self):
        name = self.cb_del.currentText()
        if not name:
            return
        r = QMessageBox.question(self, "确认", f"确定删除人格 {name}？此操作不可撤销。")
        if r != QMessageBox.Yes:
            return
        run_async(self, services.delete_persona, self._on_deleted, name)

    def _on_deleted(self, msg):
        if msg == "OK":
            QMessageBox.information(self, "完成", "已删除")
            self.refresh()
        else:
            QMessageBox.warning(self, "删除失败", str(msg))