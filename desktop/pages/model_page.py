# desktop/pages/model_page.py
# 职责：模型切换页——Ollama 模型列表 + 生成参数

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QComboBox, QDoubleSpinBox, QSpinBox, QFormLayout, QMessageBox,
)

from desktop import services
from desktop.worker import run_async


class ModelPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        box = QGroupBox("Ollama 本地模型")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        self.cb_model = QComboBox()
        row.addWidget(self.cb_model, 1)
        btn_reload = QPushButton("刷新")
        btn_reload.clicked.connect(self.refresh)
        row.addWidget(btn_reload)
        btn_apply = QPushButton("应用模型")
        btn_apply.clicked.connect(self.apply_model)
        row.addWidget(btn_apply)
        v.addLayout(row)
        layout.addWidget(box)

        gen = QGroupBox("生成参数")
        form = QFormLayout(gen)
        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 2.0)
        self.sp_temp.setSingleStep(0.05)
        self.sp_temp.setValue(0.7)
        form.addRow("temperature", self.sp_temp)

        self.sp_ctx = QSpinBox()
        self.sp_ctx.setRange(512, 32768)
        self.sp_ctx.setSingleStep(512)
        self.sp_ctx.setValue(8192)
        form.addRow("num_ctx", self.sp_ctx)

        self.sp_pred = QSpinBox()
        self.sp_pred.setRange(64, 4096)
        self.sp_pred.setSingleStep(64)
        self.sp_pred.setValue(1024)
        form.addRow("num_predict", self.sp_pred)

        btn_gen = QPushButton("应用参数")
        btn_gen.clicked.connect(self.apply_generation)
        form.addRow(btn_gen)
        layout.addWidget(gen)

        layout.addStretch()
        self.refresh()

    def refresh(self):
        run_async(self, services.list_ollama_models, self._fill_models)
        run_async(self, services.get_generation, self._fill_gen)

    def _fill_models(self, models):
        self.cb_model.clear()
        self.cb_model.addItems(models)
        cur = services.get_active().get("model")
        idx = self.cb_model.findText(cur)
        if idx >= 0:
            self.cb_model.setCurrentIndex(idx)

    def _fill_gen(self, g):
        if g.get("temperature") is not None:
            self.sp_temp.setValue(float(g["temperature"]))
        if g.get("num_ctx") is not None:
            self.sp_ctx.setValue(int(g["num_ctx"]))
        if g.get("num_predict") is not None:
            self.sp_pred.setValue(int(g["num_predict"]))

    def apply_model(self):
        m = self.cb_model.currentText()
        if not m:
            return
        run_async(self, services.apply_active, self._done, {"model": m, "provider": "ollama"})

    def apply_generation(self):
        patch = {
            "temperature": self.sp_temp.value(),
            "num_ctx": self.sp_ctx.value(),
            "num_predict": self.sp_pred.value(),
        }
        run_async(self, services.apply_generation, self._done, patch)

    def _done(self, _):
        QMessageBox.information(self, "完成", "已更新，下一轮对话生效")