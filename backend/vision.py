# backend/vision.py
# 职责：L3 能力层——图片理解（视觉模型直连 + 指纹缓存 + 尺寸/频率护栏）
# 原则：
#   1. 只处理小图（QQ 表情包，默认边长 ≤512），大图跳过、不缩放不识别
#   2. 同一张图只识别一次：按内容 sha256 指纹缓存，命中直接复用描述
#   3. 图片 base64 只进模型请求，绝不写入记忆库（归档只留文字描述）

import base64
import hashlib
import io
import os
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

DEFAULT_PROMPT = ("用一句中文描述这张图片/表情包的内容和情绪，不超过40字。"
                  "只描述你看到的，不要解释，不要加引号。")

DEFAULTS = {
    "enabled": True,
    "model": "",                    # 空 = 跟随当前对话模型（直连 27B）
    "max_side": 512,                # 最长边超过这个值就不识别
    "max_bytes": 2 * 1024 * 1024,   # 原始文件上限
    "max_images": 2,                # 单条消息最多处理几张
    "max_per_minute": 10,           # 视觉调用频率上限（防群刷图拖垮回复）
    "cache_path": "memory/index/vision_cache.sqlite3",
    "timeout": 120,
}


class VisionCache:
    """图片指纹 → 描述 的本地缓存（SQLite）"""

    def __init__(self, path: str):
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS vision_cache (
                    fingerprint TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    model TEXT DEFAULT '',
                    width INTEGER DEFAULT 0,
                    height INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT '',
                    hits INTEGER DEFAULT 0
                )
            """)

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def get(self, fingerprint: str):
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT * FROM vision_cache WHERE fingerprint=?",
                (fingerprint,)).fetchone()
            if not row:
                return None
            con.execute("UPDATE vision_cache SET hits=hits+1 WHERE fingerprint=?",
                        (fingerprint,))
        return dict(row)

    def put(self, fingerprint: str, description: str, model: str = "",
            width: int = 0, height: int = 0):
        with self._lock, self._connect() as con:
            con.execute("""
                INSERT INTO vision_cache(fingerprint, description, model, width, height, created_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    description=excluded.description,
                    model=excluded.model,
                    width=excluded.width,
                    height=excluded.height,
                    created_at=excluded.created_at
            """, (fingerprint, description, model, width, height,
                  time.strftime("%Y-%m-%dT%H:%M:%S")))

    def stats(self) -> dict:
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM vision_cache").fetchone()[0]
            hits = con.execute("SELECT COALESCE(SUM(hits),0) FROM vision_cache").fetchone()[0]
        return {"cached": total, "hits": hits}


class VisionPipeline:
    """图片识别管线：解码 → 尺寸校验 → 指纹查缓存 → 视觉模型（仅未命中）"""

    def __init__(self, cfg: dict = None, cache: VisionCache = None,
                 provider_call=None, project_root: str = None):
        cfg = dict(DEFAULTS, **(cfg or {}))
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.model = str(cfg.get("model") or "").strip()
        self.max_side = int(cfg.get("max_side", 512))
        self.max_bytes = int(cfg.get("max_bytes", 2 * 1024 * 1024))
        self.max_images = int(cfg.get("max_images", 2))
        self.max_per_minute = int(cfg.get("max_per_minute", 10))
        self.prompt = str(cfg.get("describe_prompt") or DEFAULT_PROMPT)

        root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
        cache_path = Path(cfg.get("cache_path") or DEFAULTS["cache_path"])
        if not cache_path.is_absolute():
            cache_path = root / cache_path
        self.cache = cache or VisionCache(str(cache_path))
        self._stamps = deque(maxlen=200)
        self._lock = threading.Lock()

        if provider_call is None:
            from backend.llm import provider
            provider_call = provider.chat
        self.call = provider_call

    # ---------- 取图 ----------

    @staticmethod
    def load_bytes(seg: dict) -> bytes:
        """
        从 OneBot image 段取原始字节。支持：
          - data.url / data.file 为 http(s)
          - data.file 为本地路径（宿主机）
          - data.file 为 base64://
        """
        import requests
        data = seg or {}
        url = data.get("url") or ""
        file = data.get("file") or data.get("file_id") or ""

        if isinstance(file, str) and file.startswith("base64://"):
            return base64.b64decode(file[len("base64://"):])
        if isinstance(url, str) and url.startswith("http"):
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return r.content
        if isinstance(file, str) and file.startswith("http"):
            r = requests.get(file, timeout=10)
            r.raise_for_status()
            return r.content
        # NapCat 有时直接把本地路径放进 file/url
        for cand in (file, url):
            if isinstance(cand, str) and cand and os.path.isfile(cand):
                with open(cand, "rb") as f:
                    return f.read()
        return b""

    # ---------- 校验与指纹 ----------

    def inspect(self, raw: bytes) -> dict:
        """解码 + 尺寸检查 + 生成指纹。ok=False 时不进入识别"""
        if not raw:
            return {"ok": False, "reason": "empty"}
        if len(raw) > self.max_bytes:
            return {"ok": False, "reason": "too_big_bytes", "bytes": len(raw)}
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            img.seek(0)              # 动图只取第一帧
            width, height = img.size
        except Exception as e:
            return {"ok": False, "reason": f"decode_failed: {e}"}

        if max(width, height) > self.max_side:
            return {"ok": False, "reason": "too_large",
                    "width": width, "height": height}

        fingerprint = hashlib.sha256(raw).hexdigest()
        # 统一转成 PNG 再送模型，去掉元数据、动图只留一帧
        try:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            png = buf.getvalue()
        except Exception as e:
            return {"ok": False, "reason": f"encode_failed: {e}"}

        return {"ok": True, "fingerprint": fingerprint, "width": width,
                "height": height, "b64": base64.b64encode(png).decode()}

    # ---------- 识别 ----------

    def cached_description(self, fingerprint: str) -> str:
        try:
            row = self.cache.get(fingerprint)
            return (row or {}).get("description", "")
        except Exception:
            return ""

    def _rate_limited(self) -> bool:
        now = time.time()
        with self._lock:
            while self._stamps and now - self._stamps[0] > 60:
                self._stamps.popleft()
            if len(self._stamps) >= self.max_per_minute:
                return True
            self._stamps.append(now)
            return False

    def describe(self, raw: bytes) -> dict:
        """
        识别一张图。返回：
          {"ok":True,"description":...,"fingerprint":...,"cached":bool,"width","height"}
          {"ok":False,"reason":...}
        """
        info = self.inspect(raw)
        if not info.get("ok"):
            return {"ok": False, "reason": info.get("reason", "invalid"),
                    "width": info.get("width"), "height": info.get("height")}

        fp = info["fingerprint"]
        hit = self.cached_description(fp)
        if hit:
            return {"ok": True, "description": hit, "fingerprint": fp,
                    "cached": True, "width": info["width"], "height": info["height"]}

        if self._rate_limited():
            return {"ok": False, "reason": "rate_limited", "fingerprint": fp,
                    "width": info["width"], "height": info["height"]}

        messages = [{"role": "user", "content": self.prompt,
                     "images": [info["b64"]]}]
        try:
            if self.model:
                raw_text = self.call(messages, model_override=self.model)
            else:
                raw_text = self.call(messages)
        except Exception as e:
            return {"ok": False, "reason": f"model_error: {e}", "fingerprint": fp}

        desc = self._clean(raw_text)
        if not desc:
            return {"ok": False, "reason": "empty_description", "fingerprint": fp}

        try:
            self.cache.put(fp, desc, model=self.model or "active",
                           width=info["width"], height=info["height"])
        except Exception as e:
            print(f"[Vision] 缓存写入失败: {e}")
        return {"ok": True, "description": desc, "fingerprint": fp,
                "cached": False, "width": info["width"], "height": info["height"]}

    @staticmethod
    def _clean(text: str) -> str:
        """去掉引号、换行、多余空白，控制在一句话内"""
        if not text:
            return ""
        t = str(text).strip().strip('"“”\'')
        t = t.replace("\n", " ").strip()
        t = t.split("。")[0].strip("，, ")
        return t[:60]

    def stats(self) -> dict:
        return {"enabled": self.enabled, "max_side": self.max_side,
                "cache": self.cache.stats()}
