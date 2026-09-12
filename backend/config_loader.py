# backend/config_loader.py
# 职责：统一加载项目全局配置（单例模式，避免重复读盘）

from pathlib import Path
import yaml

_config = None


def load_config(reload: bool = False) -> dict:
    """加载配置文件（默认缓存）"""
    global _config
    if _config is not None and not reload:
        return _config

    base_dir = Path(__file__).resolve().parent.parent
    config_path = base_dir / "config" / "app_config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)

    return _config


def get(path: str, default=None):
    """
    通过点号路径访问配置
    用法: get("model.base") → "qwen3.5:9b"
    """
    config = load_config()
    keys = path.split(".")
    value = config
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return default
    return value