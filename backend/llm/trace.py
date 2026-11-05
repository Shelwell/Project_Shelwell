# backend/llm/trace.py
# 职责：L2 调试追踪——把每次对话实际送入模型的 messages、原始输出、
#       最终清洗结果、模型参数、耗时、用量和错误落盘，供提示词优化。
#
# 设计：
#   - 每次调用一个 JSON 文件：logs/llm_trace/YYYY-MM-DD/<时间>_<类型>_<id>.json
#   - 另写 index.jsonl 供控制台快速列表；大字段只在单文件里
#   - 图片 base64 替换成占位符，避免 trace 被图片撑爆
#   - 按 debug.trace_retention_days 清理旧日期目录
#   - trace_enabled=false 时完全 no-op

from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.config_loader import get

_LOCK = threading.Lock()
_LAST_PRUNE = 0.0


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _trace_dir() -> Path:
    raw = str(get("debug.trace_dir", "logs/llm_trace") or "logs/llm_trace")
    p = Path(raw)
    if not p.is_absolute():
        p = _project_root() / p
    return p


def _enabled() -> bool:
    return bool(get("debug.trace_enabled", True))


def _runtime_values() -> dict:
    """provider/model/生成参数快照（只用于展示，不参与推理）"""
    try:
        from backend.runtime_config import get_runtime
        rt = get_runtime()
        provider = str(rt.get("active.provider") or "ollama")
        model = str(rt.get("active.model") or "")
        keep = rt.get("generation.keep_alive")
        if keep is None:
            keep = get("generation.keep_alive", "")
        return {
            "provider": provider,
            "model": model,
            "temperature": rt.get("generation.temperature",
                                  get("generation.temperature", 0.7)),
            "num_ctx": rt.get("generation.num_ctx",
                              get("generation.num_ctx", 8192)),
            "num_predict": rt.get("generation.num_predict",
                                  get("generation.num_predict", 1024)),
            "keep_alive": keep,
            "think": rt.get("generation.think",
                            get("generation.think", False)),
            "top_p": rt.get("generation.top_p", get("generation.top_p", None)),
            "repeat_penalty": rt.get("generation.repeat_penalty",
                                     get("generation.repeat_penalty", None)),
        }
    except Exception:
        return {"provider": "", "model": "", "temperature": None,
                "num_ctx": None, "num_predict": None,
                "keep_alive": "", "think": None,
                "top_p": None, "repeat_penalty": None}


def _sanitize_messages(messages: list) -> list:
    """图片 base64 换成占位符，其余字段原样保留"""
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        d = dict(m)
        images = d.get("images")
        if images:
            d["images"] = ["<image base64 %d chars>" % len(str(x))
                           for x in images]
        out.append(d)
    return out


def _msg_chars(messages: list) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages or []
               if isinstance(m, dict))


def _prune_old(trace_dir: Path):
    """删掉超过保留天数的日期目录；一小时最多跑一次"""
    global _LAST_PRUNE
    now = time.time()
    if now - _LAST_PRUNE < 3600:
        return
    _LAST_PRUNE = now
    try:
        days = int(get("debug.trace_retention_days", 7) or 7)
        if days <= 0 or not trace_dir.exists():
            return
        cutoff = datetime.now().date().toordinal() - days
        for p in trace_dir.iterdir():
            if not p.is_dir():
                continue
            try:
                ordinal = datetime.strptime(p.name, "%Y-%m-%d").date().toordinal()
            except ValueError:
                continue
            if ordinal < cutoff:
                shutil.rmtree(p, ignore_errors=True)
    except Exception as e:
        print(f"[Trace] 清理旧日志失败: {e}")


class Trace:
    """一次模型调用的追踪记录（start_trace 创建，finish 落盘）"""

    def __init__(self, messages: list, meta: dict = None, kind: str = "chat"):
        self.enabled = _enabled()
        self.kind = str(kind or "chat")
        self.meta = dict(meta or {})
        self.messages = _sanitize_messages(messages)
        self.started = time.time()
        self.trace_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.values = _runtime_values()
        try:
            self.max_chars = int(get("debug.trace_max_chars", 1000000) or 1000000)
        except Exception:
            self.max_chars = 1000000

    def finish(self, raw_reply: str = "", cleaned_reply: str = "",
               bubbles: list = None, silent: bool = False,
               error: str = "", error_detail: str = ""):
        if not self.enabled:
            return
        try:
            now = time.time()
            duration_ms = int((now - self.started) * 1000)
            messages = self.messages
            total = _msg_chars(messages)
            # 极端长上下文兜底：只截断中间历史，保留 system 和最后一条 user
            if total > self.max_chars and len(messages) > 2:
                keep_head = messages[:1]
                keep_tail = messages[-1:]
                middle = messages[1:-1]
                budget = max(20000, self.max_chars - _msg_chars(keep_head + keep_tail))
                kept = []
                used = 0
                for m in reversed(middle):
                    c = len(str(m.get("content", ""))) if isinstance(m, dict) else 0
                    if used + c > budget:
                        break
                    kept.append(m)
                    used += c
                kept.reverse()
                messages = keep_head + kept + keep_tail

            usage = {}
            try:
                from backend.llm.usage import get_tracker
                usage = get_tracker().last or {}
            except Exception:
                pass

            system_chars = len(str(messages[0].get("content", ""))) if messages else 0
            last_user = ""
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    last_user = str(m.get("content", ""))
                    break

            record = {
                "trace_id": self.trace_id,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": self.kind,
                "provider": self.values.get("provider", ""),
                "model": self.values.get("model", ""),
                "params": {
                    "temperature": self.values.get("temperature"),
                    "num_ctx": self.values.get("num_ctx"),
                    "num_predict": self.values.get("num_predict"),
                    "keep_alive": self.values.get("keep_alive"),
                    "think": self.values.get("think"),
                    "top_p": self.values.get("top_p"),
                    "repeat_penalty": self.values.get("repeat_penalty"),
                },
                "meta": self.meta,
                "prompt": {
                    "n_messages": len(messages),
                    "total_chars": _msg_chars(messages),
                    "system_chars": system_chars,
                    "last_user_chars": len(last_user),
                },
                "messages": messages,
                "raw_reply": str(raw_reply or ""),
                "cleaned_reply": str(cleaned_reply or ""),
                "bubbles": list(bubbles or []),
                "silent": bool(silent),
                "ok": not bool(error),
                "error": str(error or ""),
                "error_detail": str(error_detail or ""),
                "duration_ms": duration_ms,
                "usage": usage,
            }

            trace_dir = _trace_dir()
            day_dir = trace_dir / datetime.now().strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            filename = "%s_%s_%s.json" % (
                datetime.now().strftime("%H%M%S"), self.kind, self.trace_id)
            path = day_dir / filename
            with _LOCK:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False, indent=2)
                index = {
                    "trace_id": self.trace_id,
                    "ts": record["ts"],
                    "kind": self.kind,
                    "provider": record["provider"],
                    "model": record["model"],
                    "session_id": self.meta.get("session_id", ""),
                    "speaker": self.meta.get("speaker", ""),
                    "ok": record["ok"],
                    "error": record["error"][:200],
                    "duration_ms": duration_ms,
                    "prompt_chars": record["prompt"]["total_chars"],
                    "n_messages": record["prompt"]["n_messages"],
                    "reply_chars": len(record["cleaned_reply"]),
                    "silent": record["silent"],
                    "file": str(path.relative_to(trace_dir)).replace("\\", "/"),
                }
                with open(trace_dir / "index.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(index, ensure_ascii=False) + "\n")
            _prune_old(trace_dir)
        except Exception as e:
            print(f"[Trace] 写入失败: {e}")


def start_trace(messages: list, meta: dict = None, kind: str = "chat") -> Trace:
    return Trace(messages, meta=meta, kind=kind)


def format_error(exc: Exception) -> str:
    try:
        return traceback.format_exc()[-4000:]
    except Exception:
        return str(exc)
