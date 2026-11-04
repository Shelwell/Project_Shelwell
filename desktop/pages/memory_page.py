# desktop/pages/memory_page.py
# 职责：记忆管理页——统计、清空、情景/语义记忆查看与搜索

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QMessageBox, QFrame, QHeaderView, QCheckBox, QTimeEdit,
)
from PySide6.QtCore import QTimer, Qt, QTime
from PySide6.QtGui import QFont

from desktop import services
from desktop.worker import run_async


class MemoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # ---------- 统计 ----------
        stat_box = QGroupBox("统计")
        sl = QVBoxLayout(stat_box)
        row1 = QHBoxLayout()
        self.lab_stat = QLabel("加载中…")
        self.lab_stat.setStyleSheet("color: #444;")
        row1.addWidget(self.lab_stat)
        row1.addStretch()
        btn_refresh = QPushButton("刷新统计")
        btn_refresh.clicked.connect(self.refresh_stats)
        row1.addWidget(btn_refresh)
        sl.addLayout(row1)
        layout.addWidget(stat_box)

        # ---------- 清空 ----------
        clr_box = QGroupBox("清空")
        cl = QHBoxLayout(clr_box)
        self.btn_clear = QPushButton("清空全部记忆")
        self.btn_clear.setStyleSheet(
            "QPushButton{background:#888;color:white;border-radius:4px;padding:4px 12px;}")
        self.btn_clear.clicked.connect(self.on_clear_clicked)
        cl.addWidget(self.btn_clear)
        warn = QLabel("三次点击确认后删除全部归档、向量、关键词索引与关系等级，不可恢复。")
        warn.setStyleSheet("color: #b33; font-size: 12px;")
        cl.addWidget(warn)
        cl.addStretch()
        layout.addWidget(clr_box)

        # ---------- 提炼 ----------
        con_box = QGroupBox("提炼")
        cv = QVBoxLayout(con_box)

        r0 = QHBoxLayout()
        self.btn_consolidate = QPushButton("提炼情景记忆 → 语义事实")
        self.btn_consolidate.clicked.connect(self.do_consolidate)
        r0.addWidget(self.btn_consolidate)
        self.lab_con = QLabel("将未提炼的情景记忆交给模型，提取稳定事实存入语义库。")
        self.lab_con.setStyleSheet("color: #888; font-size: 12px;")
        r0.addWidget(self.lab_con)
        r0.addStretch()
        cv.addLayout(r0)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("提炼模型"))
        self.cb_ex_model = QComboBox()
        self.cb_ex_model.setMinimumWidth(210)
        self.cb_ex_model.setToolTip(
            "语义提炼需要生成模型；bge-m3 是嵌入模型，只能做向量检索，无法提炼")
        r1.addWidget(self.cb_ex_model)

        self.chk_auto = QCheckBox("自动提炼")
        self.chk_auto.stateChanged.connect(lambda _=0: self._update_sched_label())
        r1.addWidget(self.chk_auto)

        self.ed_time = QTimeEdit()
        self.ed_time.setDisplayFormat("HH:mm")
        self.ed_time.setTime(QTime(23, 0))
        self.ed_time.timeChanged.connect(lambda _=None: self._update_sched_label())
        r1.addWidget(self.ed_time)

        btn_save = QPushButton("保存设置")
        btn_save.clicked.connect(self.save_schedule)
        r1.addWidget(btn_save)

        self.lab_sched = QLabel("")
        self.lab_sched.setStyleSheet("color: #888; font-size: 12px;")
        r1.addWidget(self.lab_sched)
        r1.addStretch()
        cv.addLayout(r1)

        layout.addWidget(con_box)

        self._clear_count = 0
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(5000)
        self._clear_timer.timeout.connect(self._reset_clear)

        # ---------- 搜索筛选 ----------
        flt_box = QGroupBox("查看记忆")
        fl = QVBoxLayout(flt_box)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("类型"))
        self.cb_type = QComboBox()
        self.cb_type.addItems(["情景记忆", "语义记忆"])
        self.cb_type.currentIndexChanged.connect(self.on_type_changed)
        r1.addWidget(self.cb_type)
        r1.addWidget(QLabel("会话"))
        self.cb_session = QComboBox()
        self.cb_session.addItem("全部", "")
        self.cb_session.setMinimumWidth(180)
        r1.addWidget(self.cb_session)
        r1.addStretch()
        fl.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("关键词"))
        self.ed_keyword = QLineEdit()
        self.ed_keyword.setPlaceholderText("搜索对话内容")
        r2.addWidget(self.ed_keyword, 1)
        r2.addWidget(QLabel("时间"))
        self.ed_from = QLineEdit()
        self.ed_from.setPlaceholderText("YYYY-MM-DD")
        self.ed_from.setMaximumWidth(110)
        r2.addWidget(self.ed_from)
        r2.addWidget(QLabel("至"))
        self.ed_to = QLineEdit()
        self.ed_to.setPlaceholderText("YYYY-MM-DD")
        self.ed_to.setMaximumWidth(110)
        r2.addWidget(self.ed_to)
        btn_search = QPushButton("搜索")
        btn_search.clicked.connect(lambda: self.load_page(1))
        r2.addWidget(btn_search)
        btn_reset = QPushButton("重置")
        btn_reset.clicked.connect(self.reset_filters)
        r2.addWidget(btn_reset)
        fl.addLayout(r2)

        # ---------- 表格 ----------
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["时间", "会话", "发言人", "用户消息", "AI 回复", "操作"])
        self.table.setFont(QFont("Microsoft YaHei", 9))
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self._apply_header_modes()
        self.table.setMinimumHeight(280)
        fl.addWidget(self.table)

        # ---------- 分页 ----------
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
        self.lab_total.setStyleSheet("color: #888;")
        pg.addWidget(self.lab_total)
        fl.addLayout(pg)

        layout.addWidget(flt_box, 1)

        self._cur_page = 1
        self._pages = 1
        self._semantic_placeholder = None
        self.refresh_stats()
        self.reload_sessions()
        self.load_page(1)
        self.load_schedule()

    # ---------- 统计 ----------

    def refresh_stats(self):
        run_async(self, services.get_memory_stats, self._on_stats)

    def _on_stats(self, st):
        if st.get("error"):
            self.lab_stat.setText("统计读取失败：%s" % str(st["error"])[:80])
            return
        sem = st.get("semantic", {}) or {}
        self.lab_stat.setText(
            "情景 %d 条 | 向量 %d 条 | 关键词 %d 条 | 语义 %d 条 | 会话 %d 个"
            % (st.get("archive", 0), st.get("vector", 0),
               st.get("lexical", 0), sem.get("total", 0), st.get("sessions", 0)))

    # ---------- 清空 ----------

    def on_clear_clicked(self):
        self._clear_count += 1
        n = self._clear_count
        if n == 1:
            self.btn_clear.setText("清空全部记忆 (1/3) 点击确认")
            self.btn_clear.setStyleSheet(
                "QPushButton{background:#b8860b;color:white;border-radius:4px;padding:4px 12px;}")
            self._clear_timer.start()
        elif n == 2:
            self.btn_clear.setText("确认清空？再点一次 (2/3)")
            self.btn_clear.setStyleSheet(
                "QPushButton{background:#c60;color:white;border-radius:4px;padding:4px 12px;}")
            self._clear_timer.start()
        else:
            self._clear_timer.stop()
            self.btn_clear.setText("正在清空…")
            self.btn_clear.setEnabled(False)
            run_async(self, services.clear_all_memory, self._on_cleared)

    def _reset_clear(self):
        self._clear_count = 0
        self.btn_clear.setText("清空全部记忆")
        self.btn_clear.setEnabled(True)
        self.btn_clear.setStyleSheet(
            "QPushButton{background:#888;color:white;border-radius:4px;padding:4px 12px;}")

    def _on_cleared(self, log):
        self._reset_clear()
        QMessageBox.information(self, "完成", "记忆已清空。\n\n" + str(log))
        self.refresh_stats()
        self.reload_sessions()
        self.load_page(1)

    # ---------- 会话列表 ----------

    def reload_sessions(self):
        run_async(self, services.list_episodic_sessions, self._on_sessions)

    def _on_sessions(self, sessions):
        cur = self.cb_session.currentData()
        self.cb_session.blockSignals(True)
        self.cb_session.clear()
        self.cb_session.addItem("全部", "")
        for s in sessions:
            self.cb_session.addItem(s, s)
        idx = self.cb_session.findData(cur)
        self.cb_session.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_session.blockSignals(False)

    # ---------- 查看 ----------

    def on_type_changed(self):
        self.table.clearSpans()
        # 必须先清空行：否则上一种模式留下的“删除”按钮会残留在新表格的单元格上
        # （情景模式的操作列索引 5，正好是语义模式的“状态”列）
        self.table.setRowCount(0)
        self.btn_prev.setEnabled(True)
        self.btn_next.setEnabled(True)
        if self.cb_type.currentIndex() == 1:
            # 语义记忆：7 列信息 + 操作
            self.table.setColumnCount(8)
            self.table.setHorizontalHeaderLabels(
                ["事实", "类型", "权重", "命中", "置信度", "状态", "归档", "操作"])
        else:
            self.table.setColumnCount(6)
            self.table.setHorizontalHeaderLabels(
                ["时间", "会话", "发言人", "用户消息", "AI 回复", "操作"])
        self._apply_header_modes()
        self.load_page(1)

    def _apply_header_modes(self):
        """列宽可拖动：全部 Interactive，切换类型时套用一组默认宽度"""
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(40)
        n = self.table.columnCount()
        defaults = {
            6: [150, 110, 90, 260, 260],
            8: [320, 90, 60, 50, 64, 70, 64],
        }.get(n, [])
        for i in range(n):
            if i == n - 1:
                self.table.setColumnWidth(i, 64)      # 操作列
            else:
                self.table.setColumnWidth(
                    i, defaults[i] if i < len(defaults) else 100)

    def reset_filters(self):
        self.ed_keyword.clear()
        self.ed_from.clear()
        self.ed_to.clear()
        self.cb_session.setCurrentIndex(0)
        self.load_page(1)

    def load_page(self, page: int):
        kw = self.ed_keyword.text().strip()
        if self.cb_type.currentIndex() == 1:
            # 语义记忆
            run_async(self, services.list_semantic_facts, self._on_semantic,
                      page, 20, "", kw)
            return
        dfrom = self.ed_from.text().strip()
        dto = self.ed_to.text().strip()
        sess = self.cb_session.currentData() or ""
        run_async(self, services.list_episodic_memories, self._on_memories,
                  page, 20, sess, kw, dfrom, dto)

    def _on_semantic(self, r):
        if r.get("error"):
            self.lab_total.setText("加载失败：%s" % str(r["error"])[:60])
            return
        self._cur_page = r["page"]
        self._pages = r["pages"]
        items = r["items"]
        self.table.setRowCount(0)   # 清掉上一页残留的按钮控件
        self.table.setRowCount(len(items))
        for i, f in enumerate(items):
            archived = f.get("archived", False)
            vals = [
                f.get("fact", ""),
                f.get("tag", "misc"),
                "%.2f" % f.get("weight", 0),
                str(f.get("hits", 0)),
                "%.2f" % f.get("confidence", 0),
                f.get("state", ""),
                "已归档" if archived else "有效",
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setToolTip(str(v))
                if archived:
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor("#999"))
                self.table.setItem(i, c, item)
            self.table.setCellWidget(i, 7, self._delete_btn("semantic", f.get("id", "")))
        self.lab_page.setText("第 %d / %d 页" % (self._cur_page, self._pages))
        self.lab_total.setText("共 %d 条" % r["total"])
        self.btn_prev.setEnabled(self._cur_page > 1)
        self.btn_next.setEnabled(self._cur_page < self._pages)

    def do_consolidate(self):
        self.btn_consolidate.setEnabled(False)
        self.btn_consolidate.setText("提炼中，请稍候（需调用模型，可能较慢）…")
        run_async(self, services.run_consolidate, self._on_consolidated, "", 50, False)

    def _on_consolidated(self, r):
        self.btn_consolidate.setEnabled(True)
        self.btn_consolidate.setText("提炼情景记忆 → 语义事实")
        if r.get("error"):
            QMessageBox.warning(self, "提炼失败", str(r["error"])[:200])
            return
        QMessageBox.information(
            self, "提炼完成",
            "处理情景 %d 条\n新增事实 %d 条\n合并事实 %d 条"
            % (r.get("processed", 0), r.get("facts_new", 0), r.get("facts_merged", 0)))
        self.refresh_stats()
        self.load_page(1)

    # ---------- 自动提炼设置 ----------

    def load_schedule(self):
        run_async(self, services.get_memory_schedule, self._on_schedule)

    def _on_schedule(self, s):
        if s.get("error"):
            self.lab_sched.setText("读取设置失败")
            return
        cur_model = s.get("model") or ""
        self.chk_auto.setChecked(bool(s.get("auto")))
        try:
            hh, mm = str(s.get("time") or "23:00").split(":")
            self.ed_time.setTime(QTime(int(hh), int(mm)))
        except Exception:
            self.ed_time.setTime(QTime(23, 0))
        self._update_sched_label()
        run_async(self, services.list_ollama_models,
                  lambda models: self._on_models(models, cur_model))

    def _on_models(self, models, cur):
        self.cb_ex_model.blockSignals(True)
        self.cb_ex_model.clear()
        self.cb_ex_model.addItem("跟随当前对话模型", "")
        for m in models or []:
            self.cb_ex_model.addItem(m, m)
        idx = self.cb_ex_model.findData(cur)
        if cur and idx < 0:
            self.cb_ex_model.addItem(cur + "（当前设置）", cur)
            idx = self.cb_ex_model.count() - 1
        self.cb_ex_model.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_ex_model.blockSignals(False)

    def save_schedule(self):
        t = self.ed_time.time().toString("HH:mm")
        model = self.cb_ex_model.currentData() or ""
        run_async(self, services.save_memory_schedule, self._on_saved,
                  self.chk_auto.isChecked(), t, model, 50)

    def _on_saved(self, r):
        if r.get("ok"):
            self.lab_sched.setText("已保存")
            QMessageBox.information(self, "已保存", r.get("message", "设置已保存"))
        else:
            QMessageBox.warning(self, "保存失败", r.get("message", "未知错误"))

    def _update_sched_label(self):
        if not hasattr(self, "lab_sched"):
            return
        if self.chk_auto.isChecked():
            self.lab_sched.setText(
                "每天 %s 自动提炼（需 QQ 服务运行）"
                % self.ed_time.time().toString("HH:mm"))
        else:
            self.lab_sched.setText("自动提炼已关闭")

    def _on_memories(self, r):
        self.table.clearSpans()
        if r.get("error"):
            self.lab_total.setText("加载失败：%s" % str(r["error"])[:60])
            return
        self._cur_page = r["page"]
        self._pages = r["pages"]
        items = r["items"]
        self.table.setRowCount(0)   # 清掉上一页残留的按钮控件
        self.table.setRowCount(len(items))
        for i, m in enumerate(items):
            ts = (m.get("timestamp") or "")[:19].replace("T", " ")
            sess = m.get("session_id", "")
            spk = m.get("speaker_id", "")
            um = (m.get("user_msg") or "").replace("\n", " ")
            am = (m.get("ai_msg") or "").replace("\n", " ")
            for c, v in enumerate([ts, sess, spk, um, am]):
                item = QTableWidgetItem(str(v))
                item.setToolTip(str(v))
                self.table.setItem(i, c, item)
            self.table.setCellWidget(i, 5, self._delete_btn("episodic", m.get("id", "")))
        self.lab_page.setText("第 %d / %d 页" % (self._cur_page, self._pages))
        self.lab_total.setText("共 %d 条" % r["total"])
        self.btn_prev.setEnabled(self._cur_page > 1)
        self.btn_next.setEnabled(self._cur_page < self._pages)

    # ---------- 单条删除 ----------

    def _delete_btn(self, kind: str, item_id: str):
        btn = QPushButton("删除")
        btn.setStyleSheet(
            "QPushButton{color:#b33;border:1px solid #e0c0c0;border-radius:3px;"
            "padding:1px 8px;background:#fff;}"
            "QPushButton:hover{background:#fdecec;}")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(
            lambda _=False, k=kind, i=item_id: self._confirm_delete(k, i))
        return btn

    def _confirm_delete(self, kind: str, item_id: str):
        if not item_id:
            QMessageBox.warning(self, "无法删除", "这条记录没有 ID，无法单独删除。")
            return
        if kind == "semantic":
            title = "删除语义事实"
            text = "确定删除这条语义事实？\n\n她从这件事上提取的记忆会一起消失，不可恢复。"
        else:
            title = "删除情景记忆"
            text = ("确定删除这条情景记忆？\n\n"
                    "归档原文、向量索引、关键词索引会一并删除，不可恢复。")
        if QMessageBox.question(self, title, text) != QMessageBox.Yes:
            return
        fn = (services.delete_semantic_fact if kind == "semantic"
              else services.delete_episodic_memory)
        run_async(self, fn, self._on_row_deleted, item_id)

    def _on_row_deleted(self, res):
        res = res or {}
        if res.get("ok"):
            QMessageBox.information(self, "已删除", res.get("message", "删除完成"))
        else:
            QMessageBox.warning(self, "删除失败", res.get("message", "未知错误"))
        self.refresh_stats()
        if self.cb_type.currentIndex() == 0:
            self.reload_sessions()
        self.load_page(self._cur_page)

    def prev_page(self):
        if self._cur_page > 1:
            self.load_page(self._cur_page - 1)

    def next_page(self):
        if self._cur_page < self._pages:
            self.load_page(self._cur_page + 1)