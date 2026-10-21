# desktop/pages/api_page.py
# 职责：API 接入页——预设配置、推理参数、上下文管理、状态监控

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QLineEdit, QSpinBox, QFormLayout, QMessageBox,
    QComboBox, QCheckBox, QScrollArea, QFrame, QTableWidget,
    QTableWidgetItem, QProgressBar,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from desktop import services
from desktop.worker import run_async


class ApiPage(QWidget):
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

        # ---------- 当前后端 ----------
        self.lab_cur = QLabel("—")
        self.lab_cur.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.lab_cur)

        # ---------- 接口配置 ----------
        cfg_box = QGroupBox("接口配置")
        form = QFormLayout(cfg_box)

        self.cb_preset = QComboBox()
        self.presets = services.get_presets()
        for key, p in self.presets.items():
            self.cb_preset.addItem(p["label"], key)
        self.cb_preset.currentIndexChanged.connect(self._on_preset)
        form.addRow("服务商预设", self.cb_preset)

        self.ed_base = QLineEdit()
        self.ed_base.setPlaceholderText("https://api.deepseek.com")
        form.addRow("base_url", self.ed_base)

        self.ed_key = QLineEdit()
        self.ed_key.setEchoMode(QLineEdit.Password)
        form.addRow("api_key", self.ed_key)

        self.ed_model = QComboBox()
        self.ed_model.setEditable(True)
        self.ed_model.setInsertPolicy(QComboBox.NoInsert)
        self.ed_model.lineEdit().setPlaceholderText("选择或输入模型名")
        form.addRow("model", self.ed_model)

        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(10, 600)
        self.sp_timeout.setValue(120)
        form.addRow("timeout(秒)", self.sp_timeout)
        layout.addWidget(cfg_box)

        # ---------- 推理与能力 ----------
        adv_box = QGroupBox("推理与能力")
        af = QFormLayout(adv_box)

        self.cb_effort = QComboBox()
        self.cb_effort.addItem("默认（不传）", None)
        self.cb_effort.addItem("低 low", "low")
        self.cb_effort.addItem("中 medium", "medium")
        self.cb_effort.addItem("高 high", "high")
        af.addRow("推理强度", self.cb_effort)

        self.chk_vision = QCheckBox("启用图片识别")
        self.chk_vision.setToolTip("视觉管道尚未实现，开启后仅记录开关状态，实际不处理图片")
        af.addRow("", self.chk_vision)
        layout.addWidget(adv_box)

        # ---------- 上下文管理 ----------
        ctx_box = QGroupBox("上下文管理")
        cf = QFormLayout(ctx_box)
        self.chk_compress = QCheckBox("启用上下文压缩")
        cf.addRow("", self.chk_compress)
        self.sp_threshold = QSpinBox()
        self.sp_threshold.setRange(6, 100)
        self.sp_threshold.setValue(20)
        cf.addRow("触发阈值（消息条数）", self.sp_threshold)
        self.sp_keep = QSpinBox()
        self.sp_keep.setRange(2, 50)
        self.sp_keep.setValue(8)
        cf.addRow("压缩后保留最近条数", self.sp_keep)
        note = QLabel("压缩功能后端尚未实现，当前仅保存配置。")
        note.setStyleSheet("color: #888;")
        cf.addRow(note)
        layout.addWidget(ctx_box)

        # ---------- 操作按钮 ----------
        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存配置")
        btn_save.clicked.connect(self.save)
        btn_apply = QPushButton("保存并切换到远程")
        btn_apply.clicked.connect(self.apply_remote)
        btn_local = QPushButton("切回本地 Ollama")
        btn_local.clicked.connect(self.switch_local)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_apply)
        btn_row.addStretch()
        btn_row.addWidget(btn_local)
        layout.addLayout(btn_row)

        # ---------- API 状态 ----------
        st_box = QGroupBox("API 状态")
        sl = QVBoxLayout(st_box)

        test_row = QHBoxLayout()
        btn_test = QPushButton("测试连接")
        btn_test.clicked.connect(self.test)
        test_row.addWidget(btn_test)
        self.lab_test = QLabel("未测试")
        test_row.addWidget(self.lab_test, 1)
        btn_refresh = QPushButton("刷新用量")
        btn_refresh.clicked.connect(self.refresh_usage)
        test_row.addWidget(btn_refresh)
        sl.addLayout(test_row)

        self.lab_tokens = QLabel("累计用量：—")
        sl.addWidget(self.lab_tokens)

        ctx_row = QHBoxLayout()
        ctx_row.addWidget(QLabel("上下文占用"))
        self.pb_ctx = QProgressBar()
        self.pb_ctx.setRange(0, 100)
        ctx_row.addWidget(self.pb_ctx, 1)
        self.lab_ctx = QLabel("—")
        self.lab_ctx.setMinimumWidth(200)
        self.lab_ctx.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ctx_row.addWidget(self.lab_ctx)
        sl.addLayout(ctx_row)

        sl.addWidget(QLabel("最近调用记录："))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["时间", "后端", "模型", "状态", "token", "耗时"])
        self.table.setFont(QFont("Consolas", 8))
        self.table.setMinimumHeight(180)
        sl.addWidget(self.table)
        layout.addWidget(st_box)

        layout.addStretch()
        self.refresh()

    # ---------- 数据加载 ----------

    def refresh(self):
        run_async(self, services.get_provider_cfg, self._fill)
        run_async(self, services.get_active, self._on_active)
        run_async(self, services.get_context_cfg, self._fill_ctx)
        self.refresh_usage()

    def _fill(self, c):
        preset = c.get("preset", "custom")
        idx = self.cb_preset.findData(preset)
        if idx >= 0:
            self.cb_preset.setCurrentIndex(idx)
        self.ed_base.setText(c.get("base_url", ""))
        self.ed_key.setText(c.get("api_key", ""))
        self._set_model(c.get("model", ""), c.get("preset", "custom"))
        if c.get("timeout"):
            self.sp_timeout.setValue(int(c["timeout"]))
        eff = c.get("reasoning_effort")
        i = self.cb_effort.findData(eff)
        self.cb_effort.setCurrentIndex(i if i >= 0 else 0)
        self.chk_vision.setChecked(bool(c.get("vision_enabled")))

    def _fill_ctx(self, c):
        self.chk_compress.setChecked(bool(c.get("compress_enabled")))
        if c.get("compress_threshold"):
            self.sp_threshold.setValue(int(c["compress_threshold"]))
        if c.get("compress_keep_recent"):
            self.sp_keep.setValue(int(c["compress_keep_recent"]))

    def _on_active(self, a):
        self.lab_cur.setText(
            f"当前后端：{a.get('provider')} / 模型：{a.get('model')}")

    def _on_preset(self):
        key = self.cb_preset.currentData()
        p = self.presets.get(key) or {}
        if p.get("base_url"):
            self.ed_base.setText(p["base_url"])
        self._fill_model_options(p.get("models") or [])
        if p.get("model"):
            self.ed_model.setCurrentText(p["model"])

    def _fill_model_options(self, models):
        cur = self.ed_model.currentText()
        self.ed_model.blockSignals(True)
        self.ed_model.clear()
        self.ed_model.addItems(models)
        if cur:
            self.ed_model.setCurrentText(cur)
        self.ed_model.blockSignals(False)

    def _set_model(self, model, preset):
        p = self.presets.get(preset) or {}
        self._fill_model_options(p.get("models") or [])
        if model:
            self.ed_model.setCurrentText(model)

    # ---------- 保存与应用 ----------

    def _collect(self):
        return dict(
            base_url=self.ed_base.text().strip(),
            api_key=self.ed_key.text().strip(),
            model=self.ed_model.currentText().strip(),
            timeout=self.sp_timeout.value(),
            preset=self.cb_preset.currentData(),
            reasoning_effort=self.cb_effort.currentData(),
            vision_enabled=self.chk_vision.isChecked(),
        )

    def _collect_ctx(self):
        return dict(
            compress_enabled=self.chk_compress.isChecked(),
            compress_threshold=self.sp_threshold.value(),
            compress_keep_recent=self.sp_keep.value(),
        )

    def save(self):
        c = self._collect()
        run_async(self, services.apply_provider_api, self._saved,
                  c["base_url"], c["api_key"], c["model"], c["timeout"],
                  c["preset"], c["reasoning_effort"], c["vision_enabled"])
        run_async(self, services.apply_context_cfg, lambda _: None, self._collect_ctx())

    def apply_remote(self):
        c = self._collect()
        if not c["base_url"] or not c["model"]:
            QMessageBox.warning(self, "缺少配置", "base_url 和 model 必填")
            return
        self.save()
        run_async(self, services.switch_to_api, self._switched)

    def switch_local(self):
        run_async(self, services.switch_to_ollama, self._switched)

    def _saved(self, _):
        QMessageBox.information(self, "完成", "配置已保存")

    def _switched(self, _):
        QMessageBox.information(self, "完成", "已切换后端，下一轮对话生效")
        self.refresh()

    # ---------- 状态 ----------

    def test(self):
        self.lab_test.setText("测试中…")
        self.lab_test.setStyleSheet("color: #888;")
        run_async(self, services.test_api, self._on_test)

    def _on_test(self, r):
        if r.get("ok"):
            self.lab_test.setText(
                f"接入成功  耗时 {r['elapsed']}s  "
                f"tokens {r['prompt_tokens']}+{r['completion_tokens']}")
            self.lab_test.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.lab_test.setText(f"接入失败：{r.get('error', '')[:80]}")
            self.lab_test.setStyleSheet("color: #b00; font-weight: bold;")
        self.refresh_usage()

    def refresh_usage(self):
        run_async(self, services.get_usage_stats, self._on_usage)
        run_async(self, services.get_usage_recent, self._on_recent, 15)
        run_async(self, services.estimate_context_usage, self._on_ctx_usage)

    def _on_usage(self, s):
        if not s.get("count"):
            self.lab_tokens.setText("累计用量：暂无记录")
            return
        self.lab_tokens.setText(
            f"累计调用 {s['count']} 次  |  "
            f"prompt {s['prompt_tokens']}  completion {s['completion_tokens']}  "
            f"合计 {s['total_tokens']} tokens  |  "
            f"成功率 {s['ok_rate']}%  平均 {s['avg_elapsed']}s")

    def _on_ctx_usage(self, u):
        if u.get("error"):
            self.lab_ctx.setText("—")
            return
        ratio = u["ratio"]
        self.pb_ctx.setValue(min(int(ratio), 100))
        self.pb_ctx.setStyleSheet(
            "QProgressBar::chunk{background:%s;}" %
            ("#4caf50" if ratio < 50 else "#ff9800" if ratio < 80 else "#f44336"))
        self.lab_ctx.setText(
            f"{u['est_tokens']} / {u['max_ctx']} tokens（{ratio}%）")

    def _on_recent(self, recs):
        self.table.setRowCount(0)
        import datetime
        for r in reversed(recs):
            row = self.table.rowCount()
            self.table.insertRow(row)
            t = datetime.datetime.fromtimestamp(r["ts"]).strftime("%H:%M:%S")
            vals = [
                t, r["provider"], r["model"],
                "OK" if r["ok"] else "FAIL",
                f"{r['prompt_tokens']}+{r['completion_tokens']}",
                f"{r['elapsed']}s",
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 3 and not r["ok"]:
                    item.setForeground(Qt_red())
                self.table.setItem(row, c, item)
        self.table.resizeColumnsToContents()


def Qt_red():
    from PySide6.QtGui import QColor
    return QColor("#b00")