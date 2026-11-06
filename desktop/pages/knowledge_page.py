# desktop/pages/knowledge_page.py
# 职责：知识库管理页——列表、搜索、添加、编辑、删除、导入、重建索引

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from desktop import services
from desktop.worker import run_async


CATEGORIES = ["place", "person", "faction", "tech", "daily", "history", "other"]


class KnowledgeEditDialog(QDialog):
    def __init__(self, item: dict = None, next_id: str = "", parent=None):
        super().__init__(parent)
        self._edit = bool(item)
        self.setWindowTitle("编辑知识" if self._edit else "添加知识")
        self.resize(680, 680)
        item = item or {}

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.ed_id = QLineEdit(str(item.get("id") or next_id or ""))
        self.ed_id.setPlaceholderText("K001")
        if self._edit:
            self.ed_id.setReadOnly(True)
        form.addRow("ID *", self.ed_id)

        self.ed_title = QLineEdit(str(item.get("title") or ""))
        self.ed_title.setPlaceholderText("例如：萨勒姆的位置")
        form.addRow("标题 *", self.ed_title)

        self.cb_category = QComboBox()
        self.cb_category.setEditable(True)
        for c in CATEGORIES:
            self.cb_category.addItem(c)
        cat = str(item.get("category") or "place")
        idx = self.cb_category.findText(cat)
        if idx >= 0:
            self.cb_category.setCurrentIndex(idx)
        else:
            self.cb_category.setCurrentText(cat)
        form.addRow("分类 *", self.cb_category)

        self.ed_aliases = QLineEdit("，".join(item.get("aliases") or []))
        self.ed_aliases.setPlaceholderText("萨勒姆, Zalem, 天空城（逗号分隔）")
        form.addRow("别名", self.ed_aliases)

        self.ed_questions = QPlainTextEdit()
        self.ed_questions.setPlaceholderText("一行一个，例如：\n萨勒姆是什么？\n废铁城在哪儿？")
        self.ed_questions.setPlainText("\n".join(item.get("questions") or []))
        self.ed_questions.setMaximumHeight(100)
        form.addRow("触发问题", self.ed_questions)

        self.ed_content = QPlainTextEdit()
        self.ed_content.setPlaceholderText("客观世界设定正文；不要写用户记忆/关系")
        self.ed_content.setPlainText(str(item.get("content") or ""))
        self.ed_content.setMinimumHeight(170)
        form.addRow("内容 *", self.ed_content)

        self.cb_canon = QComboBox()
        self.cb_canon.setEditable(True)
        for c in ("原文", "摘要", "推导"):
            self.cb_canon.addItem(c)
        canon = str(item.get("canon") or "原文")
        idx = self.cb_canon.findText(canon)
        if idx >= 0:
            self.cb_canon.setCurrentIndex(idx)
        else:
            self.cb_canon.setCurrentText(canon)
        form.addRow("canon", self.cb_canon)

        self.ed_related = QLineEdit("，".join(item.get("related") or []))
        self.ed_related.setPlaceholderText("K001, K006（可选）")
        form.addRow("关联 ID", self.ed_related)

        self.ed_source = QLineEdit(str(item.get("source") or "desktop_ui"))
        form.addRow("来源", self.ed_source)

        root.addLayout(form)
        tip = QLabel("embed_text 会根据标题、别名、触发问题、内容自动生成。")
        tip.setStyleSheet("color:#888;font-size:12px;")
        root.addWidget(tip)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _on_save(self):
        if not self.ed_id.text().strip():
            QMessageBox.warning(self, "提示", "ID 不能为空")
            return
        if not self.ed_title.text().strip():
            QMessageBox.warning(self, "提示", "标题不能为空")
            return
        if not self.ed_content.toPlainText().strip():
            QMessageBox.warning(self, "提示", "内容不能为空")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "id": self.ed_id.text().strip(),
            "title": self.ed_title.text().strip(),
            "category": self.cb_category.currentText().strip() or "other",
            "aliases": self.ed_aliases.text().strip(),
            "questions": self.ed_questions.toPlainText().strip(),
            "content": self.ed_content.toPlainText().strip(),
            "canon": self.cb_canon.currentText().strip() or "原文",
            "related": self.ed_related.text().strip(),
            "source": self.ed_source.text().strip() or "desktop_ui",
        }


class KnowledgePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cur_page = 1
        self._pages = 1
        self._items = {}
        self._categories = []

        layout = QVBoxLayout(self)

        # 统计
        stat_box = QGroupBox("知识库")
        sv = QVBoxLayout(stat_box)
        row = QHBoxLayout()
        self.lab_stat = QLabel("加载中…")
        self.lab_stat.setStyleSheet("color:#444;")
        row.addWidget(self.lab_stat)
        row.addStretch()
        self.btn_refresh = QPushButton("刷新统计")
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_refresh)
        self.btn_import = QPushButton("从 JSONL 导入")
        self.btn_import.clicked.connect(self.on_import)
        row.addWidget(self.btn_import)
        self.btn_rebuild = QPushButton("重建索引")
        self.btn_rebuild.setStyleSheet(
            "QPushButton{background:#b8860b;color:white;border-radius:4px;padding:4px 10px;}")
        self.btn_rebuild.clicked.connect(self.on_rebuild)
        row.addWidget(self.btn_rebuild)
        sv.addLayout(row)
        self.lab_path = QLabel("")
        self.lab_path.setStyleSheet("color:#888;font-size:12px;")
        sv.addWidget(self.lab_path)
        layout.addWidget(stat_box)

        # 筛选
        filter_box = QGroupBox("筛选 / 添加")
        fv = QVBoxLayout(filter_box)
        r1 = QHBoxLayout()
        self.ed_keyword = QLineEdit()
        self.ed_keyword.setPlaceholderText("搜索 ID / 标题 / 别名 / 问题 / 内容")
        self.ed_keyword.returnPressed.connect(lambda: self.load_page(1))
        r1.addWidget(self.ed_keyword, 1)
        self.cb_category = QComboBox()
        self.cb_category.setMinimumWidth(130)
        self.cb_category.addItem("全部分类", "")
        r1.addWidget(self.cb_category)
        btn_search = QPushButton("搜索")
        btn_search.clicked.connect(lambda: self.load_page(1))
        r1.addWidget(btn_search)
        btn_reset = QPushButton("重置")
        btn_reset.clicked.connect(self.reset_filter)
        r1.addWidget(btn_reset)
        self.btn_add = QPushButton("添加知识")
        self.btn_add.setStyleSheet(
            "QPushButton{background:#2a8;color:white;border-radius:4px;padding:5px 12px;}")
        self.btn_add.clicked.connect(self.on_add)
        r1.addWidget(self.btn_add)
        fv.addLayout(r1)
        layout.addWidget(filter_box)

        # 表格
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["ID", "标题", "分类", "别名", "内容摘要", "操作"])
        self.table.setFont(QFont("Microsoft YaHei", 9))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(False)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.Stretch)
        self.table.setMinimumHeight(360)
        layout.addWidget(self.table, 1)

        # 分页
        pg = QHBoxLayout()
        self.btn_prev = QPushButton("上一页")
        self.btn_prev.clicked.connect(self.prev_page)
        pg.addWidget(self.btn_prev)
        self.lab_page = QLabel("第 1 / 1 页")
        pg.addWidget(self.lab_page)
        self.btn_next = QPushButton("下一页")
        self.btn_next.clicked.connect(self.next_page)
        pg.addWidget(self.btn_next)
        pg.addStretch()
        self.lab_total = QLabel("")
        self.lab_total.setStyleSheet("color:#888;")
        pg.addWidget(self.lab_total)
        layout.addLayout(pg)

        self.refresh()

    # ---------- 刷新 ----------

    def refresh(self):
        run_async(self, services.get_knowledge_stats, self._on_stats)
        self.load_page(self._cur_page)

    def _on_stats(self, st):
        if not st.get("ok"):
            self.lab_stat.setText("统计失败：%s" % str(st.get("error", ""))[:100])
            return
        self.lab_stat.setText(
            "索引 %s 条 | JSONL %s 条 | 分类 %d 个"
            % (st.get("indexed", 0), st.get("jsonl", 0),
               len(st.get("categories") or [])))
        self.lab_path.setText("知识源：%s" % st.get("path", ""))
        self._categories = st.get("categories") or []
        self._fill_categories()

    def _fill_categories(self):
        current = self.cb_category.currentData()
        self.cb_category.blockSignals(True)
        self.cb_category.clear()
        self.cb_category.addItem("全部分类", "")
        for c in self._categories:
            self.cb_category.addItem(c, c)
        idx = self.cb_category.findData(current or "")
        self.cb_category.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_category.blockSignals(False)

    def load_page(self, page: int = 1):
        keyword = self.ed_keyword.text().strip()
        category = self.cb_category.currentData() or ""
        run_async(self, services.list_knowledge, self._on_page,
                  keyword, category, int(page), 20)

    def _on_page(self, res):
        if res.get("error"):
            QMessageBox.warning(self, "读取失败", str(res["error"]))
            return
        self._cur_page = int(res.get("page", 1) or 1)
        self._pages = int(res.get("pages", 1) or 1)
        items = res.get("items") or []
        self._items = {str(x.get("id", "")): x for x in items}
        self.table.setRowCount(0)
        for item in items:
            self._append_row(item)
        self.lab_page.setText("第 %d / %d 页" % (self._cur_page, self._pages))
        self.lab_total.setText("共 %d 条" % int(res.get("total", 0) or 0))
        self.btn_prev.setEnabled(self._cur_page > 1)
        self.btn_next.setEnabled(self._cur_page < self._pages)

    def _append_row(self, item):
        row = self.table.rowCount()
        self.table.insertRow(row)
        vals = [
            str(item.get("id", "")),
            str(item.get("title", "")),
            str(item.get("category", "")),
            "、".join(item.get("aliases") or []),
            str(item.get("content", "")).replace("\n", " ")[:90],
        ]
        for col, val in enumerate(vals):
            cell = QTableWidgetItem(val)
            cell.setToolTip(val)
            self.table.setItem(row, col, cell)
        self.table.setRowHeight(row, 32)

        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(4)
        btn_edit = QPushButton("编辑")
        btn_edit.setFixedWidth(52)
        btn_edit.clicked.connect(lambda _=False, iid=vals[0]: self.on_edit(iid))
        h.addWidget(btn_edit)
        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(52)
        btn_del.clicked.connect(lambda _=False, iid=vals[0]: self.on_delete(iid))
        h.addWidget(btn_del)
        self.table.setCellWidget(row, 5, box)

    def _on_double_click(self, index):
        if index.row() < 0:
            return
        iid = self.table.item(index.row(), 0)
        if iid:
            self.on_edit(iid.text())

    def prev_page(self):
        if self._cur_page > 1:
            self.load_page(self._cur_page - 1)

    def next_page(self):
        if self._cur_page < self._pages:
            self.load_page(self._cur_page + 1)

    def reset_filter(self):
        self.ed_keyword.clear()
        self.cb_category.setCurrentIndex(0)
        self.load_page(1)

    # ---------- 添加/编辑 ----------

    def on_add(self):
        run_async(self, services.next_knowledge_id, self._open_add)

    def _open_add(self, next_id):
        dlg = KnowledgeEditDialog(next_id=str(next_id or "K001"), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._save(dlg.values())

    def on_edit(self, item_id):
        item = self._items.get(str(item_id))
        if not item:
            QMessageBox.warning(self, "提示", "找不到这条知识，请刷新")
            return
        dlg = KnowledgeEditDialog(item=item, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._save(dlg.values())

    def _save(self, values):
        self.btn_add.setEnabled(False)
        self.btn_add.setText("保存中…")
        run_async(self, services.save_knowledge, self._on_saved, values,
                  on_error=self._on_save_error)

    def _on_saved(self, res):
        self.btn_add.setEnabled(True)
        self.btn_add.setText("添加知识")
        if not res.get("ok", True) or res.get("error"):
            QMessageBox.warning(self, "保存失败", str(res.get("error", "")))
            return
        QMessageBox.information(self, "完成", "知识已保存并写入索引")
        self.refresh()

    def _on_save_error(self, err):
        self.btn_add.setEnabled(True)
        self.btn_add.setText("添加知识")
        QMessageBox.warning(self, "保存失败", str(err))

    # ---------- 删除 ----------

    def on_delete(self, item_id):
        item = self._items.get(str(item_id), {})
        title = str(item.get("title") or item_id)
        ret = QMessageBox.question(
            self, "删除知识",
            "确定删除 %s「%s」？\n会同时删除 JSONL 和 Chroma 索引，不可恢复。"
            % (item_id, title),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        run_async(self, services.delete_knowledge, self._on_deleted, item_id)

    def _on_deleted(self, res):
        if not res.get("ok"):
            QMessageBox.warning(self, "删除失败", str(res.get("error", "")))
            return
        QMessageBox.information(self, "完成", "已删除")
        self.refresh()

    # ---------- 导入/重建 ----------

    def on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择知识 JSONL", "", "JSONL (*.jsonl);;所有文件 (*)")
        if not path:
            return
        if QMessageBox.question(
                self, "导入知识",
                "会按 id 合并到当前 JSONL，并写入 Chroma。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        self.btn_import.setEnabled(False)
        self.btn_import.setText("导入中…")
        run_async(self, services.import_knowledge_jsonl, self._on_imported,
                  path, on_error=self._on_import_error)

    def _on_imported(self, res):
        self.btn_import.setEnabled(True)
        self.btn_import.setText("从 JSONL 导入")
        if not res.get("ok"):
            QMessageBox.warning(self, "导入失败", str(res.get("error", "")))
            return
        QMessageBox.information(
            self, "导入完成",
            "读取 %s 条，合并后 %s 条。" % (res.get("imported", 0),
                                           res.get("total", 0)))
        self.refresh()

    def _on_import_error(self, err):
        self.btn_import.setEnabled(True)
        self.btn_import.setText("从 JSONL 导入")
        QMessageBox.warning(self, "导入失败", str(err))

    def on_rebuild(self):
        if QMessageBox.question(
                self, "重建索引",
                "会清空当前 Chroma 集合并从 JSONL 全部重新嵌入。\n"
                "期间知识检索可能不可用，确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        self.btn_rebuild.setEnabled(False)
        self.btn_rebuild.setText("重建中…")
        run_async(self, services.rebuild_knowledge_index, self._on_rebuilt,
                  on_error=self._on_rebuild_error)

    def _on_rebuilt(self, res):
        self.btn_rebuild.setEnabled(True)
        self.btn_rebuild.setText("重建索引")
        if not res or res.get("error"):
            QMessageBox.warning(self, "重建失败", str((res or {}).get("error", "")))
            return
        QMessageBox.information(
            self, "完成",
            "重建完成：总 %s，成功 %s，跳过 %s"
            % (res.get("total", 0), res.get("ok", 0), res.get("skipped", 0)))
        self.refresh()

    def _on_rebuild_error(self, err):
        self.btn_rebuild.setEnabled(True)
        self.btn_rebuild.setText("重建索引")
        QMessageBox.warning(self, "重建失败", str(err))
