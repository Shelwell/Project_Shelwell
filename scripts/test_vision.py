# scripts/test_vision.py
# 职责：图片识别管线回归测试（指纹缓存/尺寸护栏/频率限制/provider 透传）
# 用法：.venv\Scripts\python.exe scripts\test_vision.py

import sys
import io
import time
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.vision import VisionPipeline, VisionCache

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


def make_png(w=240, h=240, color=(200, 60, 60)) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), (250, 245, 235))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, min(w - 10, 120), min(h - 10, 120)], fill=color)
    d.text((10, h - 20), "SHELWELL", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


tmp = Path(tempfile.mkdtemp(prefix="shelwell_vision_"))

calls = {"n": 0}


def fake_call(messages, model_override=None):
    calls["n"] += 1
    assert messages[0].get("images"), "请求里没有 images 字段"
    return "一只红色的猫在笑"


cfg = {"enabled": True, "max_side": 512, "max_bytes": 2 * 1024 * 1024,
       "max_per_minute": 10, "cache_path": str(tmp / "vision.sqlite3")}
vp = VisionPipeline(cfg, provider_call=fake_call, project_root=str(tmp))

print("[1] 指纹与缓存")
raw = make_png()
info = vp.inspect(raw)
check("小图通过校验", info.get("ok"), info)
check("指纹是 sha256", len(info.get("fingerprint", "")) == 64, info.get("fingerprint"))
check("指纹稳定", vp.inspect(raw)["fingerprint"] == info["fingerprint"])

r1 = vp.describe(raw)
check("首次识别调用模型", calls["n"] == 1 and r1.get("ok"), (calls, r1))
check("首次非缓存", r1.get("cached") is False, r1)
r2 = vp.describe(raw)
check("二次命中缓存不调模型", calls["n"] == 1 and r2.get("cached") is True, (calls, r2))
check("描述一致", r1["description"] == r2["description"], (r1, r2))
check("缓存统计", vp.cache.stats()["cached"] == 1, vp.cache.stats())

print("\n[2] 尺寸护栏（不处理大图）")
big = make_png(800, 600)
r = vp.describe(big)
check("大图被拒绝", r.get("ok") is False and r.get("reason") == "too_large", r)
check("大图未调用模型", calls["n"] == 1, calls)

print("\n[3] 字节护栏")
small = make_png(64, 64)
cfg2 = dict(cfg, max_bytes=100)
vp2 = VisionPipeline(cfg2, provider_call=fake_call, project_root=str(tmp))
r = vp2.inspect(small)
check("超过字节上限被拒绝", r.get("ok") is False and r.get("reason") == "too_big_bytes", r)

print("\n[4] 频率限制")
cfg3 = dict(cfg, max_per_minute=1, cache_path=str(tmp / "vision2.sqlite3"))
vp3 = VisionPipeline(cfg3, provider_call=fake_call, project_root=str(tmp))
vp3.describe(make_png(200, 200, (10, 10, 200)))
r = vp3.describe(make_png(210, 210, (10, 200, 10)))
check("超频被跳过", r.get("ok") is False and r.get("reason") == "rate_limited", r)

print("\n[5] provider 透传 images")
import backend.llm.provider as prov

captured = {}


class FakeResp:
    status_code = 200

    def json(self):
        return {"message": {"content": "ok"}, "prompt_eval_count": 1, "eval_count": 1}


def fake_post(url, json=None, timeout=None, headers=None):
    captured["payload"] = json
    return FakeResp()


_orig = prov.requests.post
prov.requests.post = fake_post
try:
    prov.chat([{"role": "user", "content": "看看", "images": ["AAA"]}])
finally:
    prov.requests.post = _orig
msg = captured["payload"]["messages"][0]
check("请求体保留 images", msg.get("images") == ["AAA"], msg)

print("\n[6] 适配层图片段解析")
sys.path.insert(0, str(ROOT / "adapters" / "qq_bot"))
from adapters.qq_bot.main import QQBot
msg = [{"type": "at", "data": {"qq": "123"}},
       {"type": "image", "data": {"file": "a.png", "url": "http://x/a.png"}},
       {"type": "text", "data": {"text": "看这个"}}]
imgs = QQBot.extract_images(object(), msg)
check("提取到 1 张图", len(imgs) == 1 and imgs[0]["file"] == "a.png", imgs)
check("纯文本不产生图片", QQBot.extract_images(object(), [{"type": "text", "data": {"text": "hi"}}]) == [])

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
