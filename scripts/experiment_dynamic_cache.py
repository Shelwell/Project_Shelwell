# scripts/experiment_dynamic_cache.py
# 对照实验：动态块注入是否破坏 Ollama 完整前缀缓存。
#
# 条件：
#   A(dynamic)：每轮最后一条 user = 动态块 + 当前句；历史里存原始当前句
#   B(raw)    ：每轮最后一条 user = 当前句；历史里存原始当前句
#
# 两个条件使用同一 system、同一模型、同一 num_ctx、同一组原句。
# 每组第一轮冷启动，后续轮次看 prompt_eval_count 是否下降。
# 输出：logs/cache_experiment_results.json + 终端表格

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
MODEL = "qwen3.8:27b"
URL = "http://127.0.0.1:11434/api/chat"
NUM_CTX = 16384
KEEP_ALIVE = "5m"

SYSTEM = (ROOT / "config" / "persona_prompts" / "shelwell_system.txt").read_text(
    encoding="utf-8").strip()

QUERIES = [
    "适应环境了吗？",
    "你们那边现在是什么天气？",
    "你刚才在做什么？",
    "如果有一天能出门，你想去哪？",
]


def dynamic_block(i: int) -> str:
    """模拟真实注入的动态块；每轮有变化，避免完全相同。"""
    return "\n".join([
        "【背景资料｜系统提供，不是对方的话；不要复述、不要模仿格式】",
        f"【关系｜系统判定，勿自行升降】当前说话人：测试对象｜认识 {i} 小时，直接互动 {i * 3} 条｜等级：陌生",
        "【当前说话人的稳定档案｜系统记忆，不是你的经历，不要复述】",
        "- 对方喜欢在晚上聊天",
        "- 对方最近在修一台旧机器",
        "【相关记忆｜当前说话人的事实，不是你的经历，不要复述】",
        "- 对方说过雨是酸的",
        f"【群里刚才在聊｜第 {i} 轮】",
        "- 测试对象：今天外面很吵",
        "- 另一个人：那就别出去了",
        "【背景资料结束】",
    ])


def call(messages):
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"num_ctx": NUM_CTX, "num_predict": 1, "temperature": 0},
    }
    t0 = time.time()
    r = requests.post(URL, json=payload, timeout=600)
    r.raise_for_status()
    data = r.json()
    return {
        "prompt_eval_count": data.get("prompt_eval_count"),
        "prompt_eval_duration": data.get("prompt_eval_duration"),
        "eval_count": data.get("eval_count"),
        "eval_duration": data.get("eval_duration"),
        "total_duration": data.get("total_duration"),
        "wall_ms": round((time.time() - t0) * 1000),
        "reply": (data.get("message") or {}).get("content", ""),
    }


def run_condition(name: str, with_dynamic: bool):
    history = [{"role": "system", "content": SYSTEM}]
    rows = []
    for i, query in enumerate(QUERIES, 1):
        last = (dynamic_block(i) + "\n\n【对方刚刚说】\n" + query
                if with_dynamic else query)
        messages = list(history) + [{"role": "user", "content": last}]
        res = call(messages)
        rows.append({
            "condition": name,
            "turn": i,
            "prompt_chars": sum(len(m.get("content", "")) for m in messages),
            "last_user_chars": len(last),
            **res,
        })
        print(f"{name} turn {i}: prompt_eval={res['prompt_eval_count']} "
              f"({res['prompt_eval_duration']/1e9:.2f}s) "
              f"wall={res['wall_ms']/1000:.1f}s", flush=True)
        # 关键：历史里存原始 query，而不是带动态块的版本
        history.append({"role": "user", "content": query})
        history.append({"role": "assistant", "content": "好"})
    return rows


def main():
    print("system_chars", len(SYSTEM), flush=True)
    all_rows = []
    all_rows += run_condition("A_dynamic", True)
    all_rows += run_condition("B_raw", False)
    out = Path(__file__).resolve().parent / "experiment_dynamic_cache_results.json"
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("\n=== summary ===")
    for cond in ("A_dynamic", "B_raw"):
        rows = [r for r in all_rows if r["condition"] == cond]
        print(cond)
        for r in rows:
            speed = (r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9)
                     if r["prompt_eval_duration"] else 0)
            print(f"  turn {r['turn']}: eval_tokens={r['prompt_eval_count']} "
                  f"prompt_chars={r['prompt_chars']} "
                  f"prompt_eval={r['prompt_eval_duration']/1e9:.2f}s "
                  f"speed={speed:.1f} tok/s")
    print("saved", out)


if __name__ == "__main__":
    sys.exit(main())
