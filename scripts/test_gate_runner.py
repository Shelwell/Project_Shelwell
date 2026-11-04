# scripts/test_gate_runner.py
# 职责：门禁执行器（active 模式）与群上下文身份回归测试
# 用法：.venv\Scripts\python.exe scripts\test_gate_runner.py

import sys
import time
import json
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adapters" / "qq_bot"))
sys.stdout.reconfigure(encoding="utf-8")

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp(prefix="shelwell_gate_"))

# ---------- 1. 门禁执行器 ----------
print("[1] GateRunner（active 模式）")
from gate import GateRunner

cfg = {"mode": "active", "log_path": str(tmp / "gate.jsonl"),
       "active_hours": ["19:00-24:00"], "quiet_hours": ["00:30-09:00"],
       "name_aliases": ["西尔维娅", "西儿"]}
gr = GateRunner(cfg, str(tmp))
check("active 模式生效", gr.enforce is True, gr.mode)

now = time.time()
r1 = gr.decide(group_id="g1", sender_id="1", sender_name="小明", text="西尔维娅在吗",
               ts=now, at_me=True, legacy_engage=False)
check("@我 → 接", r1["decision"]["respond"] is True, r1["decision"])
r2 = gr.decide(group_id="g1", sender_id="2", sender_name="小红", text="今天吃啥",
               ts=now + 5, legacy_engage=True)
check("冷却/无信号 → 不接", r2["decision"]["respond"] is False, r2["decision"])

lines = [json.loads(l) for l in open(tmp / "gate.jsonl", encoding="utf-8")]
check("日志已落盘", len(lines) == 2, len(lines))
check("日志记录模式", lines[0].get("mode") == "active", lines[0].get("mode"))

# ---------- 2. 群上下文带发言人昵称 ----------
print("\n[2] 群上下文身份")
from backend.memory.service import MemoryService

svc = MemoryService(auto_worker=False, archive_dir=str(tmp / "h"),
                    chroma_dir=str(tmp / "c"), fts_db=str(tmp / "f.sqlite3"),
                    jobs_db=str(tmp / "j.sqlite3"))
svc.add_memory("我喜欢打羽毛球", "", session_id="qqg:900:1",
               speaker_id="1001", channel_type="group", channel_id="900",
               speaker_name="小明")
svc.add_memory("我也喜欢", "算我一个", session_id="qqg:900:2",
               speaker_id="1002", channel_type="group", channel_id="900",
               speaker_name="小红")
msgs = svc.get_group_context("900", limit=10)
speakers = [m["speaker"] for m in msgs]
check("发言人用昵称而不是 QQ 号", "小明" in speakers and "1001" not in speakers, speakers)
check("包含机器人回复", any(m["is_bot"] for m in msgs), msgs)

# ---------- 3. 旧配置兼容 ----------
print("\n[3] 配置兼容")
cfg2 = dict(cfg, mode="shadow")
gr2 = GateRunner(cfg2, str(tmp))
check("shadow 模式不执行", gr2.enforce is False)

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
