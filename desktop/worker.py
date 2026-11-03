# desktop/worker.py
# 职责：后台任务工具——把阻塞调用丢到线程池，结果回主线程
# 独立成模块，避免 main 与 pages 之间循环导入

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QMessageBox


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    """在全局线程池执行阻塞函数，结果通过信号回主线程"""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            r = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(r)
        except Exception as e:
            self.signals.error.emit(str(e))


# 活跃任务持有表：不持有引用时 Worker/信号可能被 GC 回收，
# 导致回调触发 "Internal C++ object (WorkerSignals) already deleted"
_ACTIVE = set()


def run_async(owner, fn, on_result, *args, on_error=None, **kwargs):
    """投递任务并绑定结果回调。on_error 可选，默认弹窗提示。"""
    w = Worker(fn, *args, **kwargs)
    _ACTIVE.add(w)
    w.signals.result.connect(on_result)
    if on_error is not None:
        w.signals.error.connect(on_error)
    else:
        w.signals.error.connect(lambda e: QMessageBox.warning(owner, "执行失败", e))
    # 结果/异常都先回调 UI，再释放持有
    w.signals.result.connect(lambda _r: _ACTIVE.discard(w))
    w.signals.error.connect(lambda _e: _ACTIVE.discard(w))
    QThreadPool.globalInstance().start(w)
    return w