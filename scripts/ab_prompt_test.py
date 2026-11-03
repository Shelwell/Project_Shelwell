# scripts/ab_prompt_test.py
# 职责：A/B 对照实验——同一份真实注入的提示词，分别跑 12B 与 27B，比较输出质量与耗时
# 用法：.venv\Scripts\python.exe scripts\ab_prompt_test.py
# 说明：记忆检索关闭，只隔离"提示词 + 模型"这一个变量

import sys
import time
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import requests

from backend.context_manager import ContextManager

OLLAMA = "http://localhost:11434"
NUM_CTX = 8192          # 第 2 步：统一降到 8192
TEMPERATURE = 0.7
NUM_PREDICT = 200       # 只比较风格与服从度，压短以控制 27B 的耗时
TIMEOUT = 900

MODELS = [
    ("12B  rudy-nemo-12b", "RoseRudolph/rudy-nemo-12b-v1:12b"),
    ("9B   qwen3.5:9b", "qwen3.5:9b"),
    ("27B  qwen3.8:27b", "qwen3.8:27b"),
]

# 可选：命令行传关键字只跑某个模型，例如 `python scripts\ab_prompt_test.py 9b`
_ONLY = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if _ONLY:
    MODELS = [m for m in MODELS if _ONLY in (m[0] + m[1]).lower()]

QUESTIONS = [
    ("情绪陪伴", "在吗，今天加班到十点，累死了"),
    ("自我怀疑", "我觉得我最近干什么都不行"),
    ("日常推荐", "有啥动画推荐吗"),
    ("身份探针", "你是不是AI啊"),
]


def build_messages(cm: ContextManager, question: str) -> list:
    """复刻生产路径：人格经独立 system 消息承载"""
    return cm.prepare_context(
        [{"role": "user", "content": question}],
        current_query=question,
        session_id="ab_test",
        system_as_role=True,
    )


def call(model: str, messages: list) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "think": False,
        "stream": False,
        "keep_alive": "5m",
        "options": {
            "num_ctx": NUM_CTX,
            "temperature": TEMPERATURE,
            "num_predict": NUM_PREDICT,
        },
    }
    t0 = time.time()
    resp = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=TIMEOUT)
    elapsed = time.time() - t0
    if resp.status_code != 200:
        return {"ok": False, "elapsed": elapsed, "error": resp.text[:300]}
    data = resp.json()
    return {
        "ok": True,
        "elapsed": elapsed,
        "text": data.get("message", {}).get("content", ""),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "out_tokens": data.get("eval_count", 0),
    }


def unload(model: str):
    try:
        requests.post(f"{OLLAMA}/api/chat", json={
            "model": model, "messages": [{"role": "user", "content": "hi"}],
            "stream": False, "keep_alive": 0,
            "options": {"num_predict": 1, "num_ctx": 2048},
        }, timeout=60)
    except Exception as e:
        print(f"[卸载] {model} 失败: {e}")


def main():
    cm = ContextManager(memory_enabled=False)
    prompt_chars = len(cm.system_instruction)
    print(f"提示词长度: {prompt_chars} 字 | num_ctx={NUM_CTX} num_predict={NUM_PREDICT}")
    print("=" * 70)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    report = ROOT / "logs" / f"ab_prompt_{stamp}.md"
    lines = [
        f"# A/B 对照 {datetime.datetime.now():%Y-%m-%d %H:%M}",
        f"- 提示词: {prompt_chars} 字（记忆检索关闭，隔离模型变量）",
        f"- 参数: num_ctx={NUM_CTX} temperature={TEMPERATURE} num_predict={NUM_PREDICT}",
        "",
    ]

    for label, model in MODELS:
        print(f"\n########## {label} ##########")
        lines.append(f"\n## {label}\n")
        for tag, q in QUESTIONS:
            msgs = build_messages(cm, q)
            r = call(model, msgs)
            if not r["ok"]:
                print(f"[{tag}] 失败: {r['error']}")
                lines.append(f"### {tag} — 失败\n`{r['error']}`\n")
                continue
            print(f"\n[{tag}] 用户：{q}")
            print(f"耗时 {r['elapsed']:.1f}s | prompt {r['prompt_tokens']} tok | 输出 {r['out_tokens']} tok")
            print(f"回复：\n{r['text'].strip()}")
            lines.append(
                f"### {tag}\n用户：{q}\n\n"
                f"耗时 {r['elapsed']:.1f}s | prompt {r['prompt_tokens']} tok | 输出 {r['out_tokens']} tok\n\n"
                f"```\n{r['text'].strip()}\n```\n"
            )
        unload(model)
        print(f"\n[{label}] 已卸载，释放显存")

    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已保存: {report}")


if __name__ == "__main__":
    main()
