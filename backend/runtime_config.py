# backend/runtime_config.py
# 职责：L1 基础设施——运行期配置（config/runtime.yaml）读写
# 特点：带 mtime 缓存，服务每轮调用开销极小；写入为原子替换

import os
import shutil
import tempfile
import threading
from pathlib import Path

import yaml

_LOCK = threading.Lock()


class RuntimeConfig:
    """运行期配置管理（单例）"""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, path: str = None):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init(path)
        return cls._instance

    def _init(self, path: str = None):
        if path is None:
            base = Path(__file__).resolve().parent.parent
            path = base / "config" / "runtime.yaml"
        self.path = str(path)
        self._mtime = None
        self._data = {}
        # 文件缺失时从模板初始化，避免首次运行崩溃
        if not os.path.exists(self.path):
            example = os.path.join(os.path.dirname(self.path), "runtime.example.yaml")
            if os.path.exists(example):
                shutil.copyfile(example, self.path)
            else:
                with open(self.path, "w", encoding="utf-8") as f:
                    yaml.safe_dump({"active": {"persona": "shelwell_persona", "provider": "ollama"}}, f, allow_unicode=True)
        self._reload_if_changed(force=True)

    # ---------- 读取 ----------

    def _reload_if_changed(self, force: bool = False):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = None
        if force or mtime != self._mtime:
            with _LOCK:
                if force or mtime != self._mtime:
                    with open(self.path, "r", encoding="utf-8") as f:
                        self._data = yaml.safe_load(f) or {}
                    self._mtime = mtime

    def all(self) -> dict:
        self._reload_if_changed()
        return self._data

    def get(self, path: str, default=None):
        """点号路径读取，如 get("active.persona")"""
        self._reload_if_changed()
        cur = self._data
        for k in path.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur

    def active(self) -> dict:
        return dict(self.get("active", {}) or {})

    # ---------- 写入 ----------

    def update(self, patch: dict) -> dict:
        """深合并写入，返回新配置。原子替换文件。"""
        self._reload_if_changed()
        with _LOCK:
            new_data = _deep_merge(self._data or {}, patch)
            self._atomic_write(new_data)
            self._data = new_data
            self._mtime = os.path.getmtime(self.path)
        return new_data

    def _atomic_write(self, data: dict):
        d = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".yaml.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_runtime() -> RuntimeConfig:
    return RuntimeConfig()