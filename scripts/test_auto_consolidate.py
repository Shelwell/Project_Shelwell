# scripts/test_auto_consolidate.py
# 职责：回归测试——提炼模型可指定、自动提炼调度逻辑
# 用法：.venv\Scripts\python.exe scripts\test_auto_consolidate.py

import sys
import tempfile
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp(prefix="shelwell_auto_"))

# ---------- 1. provider 支持 model_override ----------
print("[1] provider 模型覆盖")
import backend.llm.provider as prov

captured = {}


class FakeResp:
    status_code = 200

    def json(self):
        return {"message": {"content": "ok"}, "prompt_eval_count": 1, "eval_count": 1}


def fake_post(url, json=None, timeout=None, headers=None):
    captured["payload"] = json
    return FakeResp()


_orig_post = prov.requests.post
prov.requests.post = fake_post
try:
    prov.chat([{"role": "user", "content": "hi"}], model_override="qwen3.5:9b")
finally:
    prov.requests.post = _orig_post
check("请求体使用指定模型",
      captured.get("payload", {}).get("model") == "qwen3.5:9b",
      captured.get("payload", {}).get("model"))

# ---------- 2. 提炼器读取配置并透传 ----------
print("\n[2] 提炼器模型选择")
from backend.runtime_config import get_runtime
from backend.memory.extractor import Extractor

rt = get_runtime()
old = rt.get("memory.extractor_model", "")
rt.update({"memory": {"extractor_model": "qwen3.5:9b"}})

seen = {}


def fake_call(messages, model_override=None):
    seen["model"] = model_override
    return '{"facts": []}'


ex = Extractor(provider_call=fake_call)
ex.extract([], speaker_name="小明")
check("配置被读取", ex.model == "qwen3.5:9b", ex.model)
# 有事件时才会真正调用，这里直接验证调用参数
seen.clear()
from backend.memory.models import MemoryEvent
e = MemoryEvent.create("qq:1", "我喜欢喝茶", "", speaker_id="1", speaker_name="小明")
ex.extract([e], speaker_name="小明", owner_id="1")
check("提炼调用带模型参数", seen.get("model") == "qwen3.5:9b", seen)

# 空配置 = 跟随主模型
rt.update({"memory": {"extractor_model": ""}})
check("空配置跟随主模型", Extractor(provider_call=fake_call).model == "")

# 注入实现不接受该参数时自动降级
def legacy_call(messages):
    return '{"facts": []}'

ex2 = Extractor(provider_call=legacy_call)
ex2.model = "qwen3.5:9b"
try:
    ex2.extract([e], speaker_name="小明")
    ok = True
except Exception as ex3:
    ok = False
    print("   降级失败:", ex3)
check("兼容旧调用签名", ok)

rt.update({"memory": {"extractor_model": old or ""}})

# ---------- 3. 自动提炼调度 ----------
print("\n[3] 自动提炼时间判断")
from backend.memory.service import MemoryService

svc = MemoryService(auto_worker=False,
                    archive_dir=str(tmp / "h"),
                    chroma_dir=str(tmp / "c"),
                    fts_db=str(tmp / "f.sqlite3"),
                    jobs_db=str(tmp / "j.sqlite3"))

now = datetime(2026, 11, 3, 23, 0)
check("到点且今天没跑 → 返回 limit",
      svc.auto_due_at(now, last_date="", enabled=True, hhmm="23:00", limit=30) == 30)
check("今天已跑过 → 不跑",
      svc.auto_due_at(now, last_date="2026-11-03", enabled=True, hhmm="23:00", limit=30) is None)
check("未到点 → 不跑",
      svc.auto_due_at(datetime(2026, 11, 3, 22, 59), last_date="", enabled=True,
                      hhmm="23:00", limit=30) is None)
check("开关关闭 → 不跑",
      svc.auto_due_at(now, last_date="", enabled=False, hhmm="23:00", limit=30) is None)
check("单位数小时兼容", svc.auto_due_at(datetime(2026, 11, 3, 9, 5), last_date="",
                                    enabled=True, hhmm="9:05", limit=10) == 10)
check("非法时间 → 不跑",
      svc.auto_due_at(now, last_date="", enabled=True, hhmm="abc", limit=10) is None)

# 标记文件读写
svc._auto_marker.parent.mkdir(parents=True, exist_ok=True)
svc._auto_marker.write_text("2026-11-03\n", encoding="utf-8")
check("读取最后运行日期", svc._last_auto_date() == "2026-11-03", svc._last_auto_date())

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
