# scripts/data_prep/generate_persona_data.py
# 用途：根据人格定义文件，批量生成Shelwell的人格训练数据
# 输出：persona_seed.jsonl（每行一个JSON对象）

import os
import sys
import json
import random
import time
import yaml
import requests
from pathlib import Path

# ---------- 路径配置 ----------
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # 项目根目录
CONFIG_PATH = BASE_DIR / "config" / "persona_prompts" / "shelwell_persona.yaml"
OUTPUT_PATH = Path(__file__).resolve().parent / "persona_seed.jsonl"

# ---------- Ollama 配置 ----------
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen3.5:9b"   # 可改为你实际使用的模型名

# ---------- 生成数量 ----------
TOTAL_SAMPLES = 50          # 总样本数
BATCH_SIZE = 1               # 每次请求生成1条，避免超时

# ---------- 加载人格定义 ----------
def load_persona():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# ---------- 构建生成提示词 ----------
def build_generation_prompt(persona, scenario_key, profanity_level, user_message_style):
    """
    根据人格定义、场景、粗俗度等级，构建给模型的生成指令。
    模型将扮演“数据生成器”，产生一条符合要求的用户消息 + Shelwell回复。
    """
    scenario = persona["scenarios"][scenario_key]
    traits = "\n".join(f"- {t}" for t in persona["core_traits"])
    style = persona["speech_style"]
    prof_mgmt = persona["profanity_management"]

    # 提取粗俗度功能描述
    allowed_funcs = prof_mgmt["allowed_functions"]
    func_descriptions = []
    for name, info in allowed_funcs.items():
        func_descriptions.append(f"{name}: {info['description']}，例如 {', '.join(info['examples'])}")
    func_text = "\n".join(func_descriptions)

    prompt = f"""你是一个对话数据生成器。请根据以下人格设定，生成一对符合要求的“用户消息”和“Shelwell回复”。

【Shelwell核心人格】
{traits}

【语言风格要求】
- 不使用任何表情符号
- 不使用项目符号罗列选项
- 语气自然亲切，像老朋友
- 允许在合适语境下使用不文雅用词，但必须遵循以下规则：

【粗俗度管理规则】
- 核心原则：{prof_mgmt['core_principle']}
- 允许的功能类型：
{func_text}
- 禁止的功能类型：人身攻击、歧视性表达
- 指向规则：指向事件/自己/非侮辱用户 → 允许；指向用户侮辱 → 极端情况下允许；

【本次生成要求】
- 场景：{scenario['tone']}，话题围绕 {', '.join(scenario['topics'])}
- 用户消息的粗俗度等级：{profanity_level}
- 用户消息风格：{user_message_style}
- Shelwell的回复必须符合其人格，并自然回应，不要刻意说教。

请严格输出以下JSON格式（不要有任何额外文字）：
{{"instruction": "用户说的话", "input": "", "output": "Shelwell的回复"}}
"""
    return prompt

# ---------- 调用Ollama生成单条样本 ----------
def generate_one(prompt):
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "think": False,
                "stream": False,
                "options": {
                    "temperature": 0.9,      # 稍高温度增加多样性
                    "num_predict": 8192
                }
            },
            timeout=180
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()
        # 尝试提取JSON
        start = content.find("{")
        end = content.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        data = json.loads(content[start:end])
        # 校验字段
        if all(k in data for k in ("instruction", "input", "output")):
            if data["instruction"] and data["output"]:
                return data
        return None
    except Exception as e:
        print(f"[Error] 生成失败: {e}")
        return None

# ---------- 主流程 ----------
def main():
    persona = load_persona()
    scenarios = list(persona["scenarios"].keys())
    prof_levels = ["L0 完全文雅", "L1 轻度口语", "L2 中度粗俗", "L3 重度粗俗"]
    user_styles = [
        "日常询问", "吐槽抱怨", "分享喜悦", "寻求建议",
        "情绪低落", "开玩笑", "认真讨论", "随意闲聊"
    ]

    # 如果输出文件已存在，先备份
    if OUTPUT_PATH.exists():
        backup = OUTPUT_PATH.with_suffix(".jsonl.bak")
        OUTPUT_PATH.rename(backup)
        print(f"已备份旧数据到 {backup}")

    generated = 0
    attempts = 0
    max_attempts = TOTAL_SAMPLES * 3

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        while generated < TOTAL_SAMPLES and attempts < max_attempts:
            attempts += 1
            scenario = random.choice(scenarios)
            # 粗俗度分布：L0 30%, L1 40%, L2 20%, L3 10%
            level = random.choices(prof_levels, weights=[0.3, 0.4, 0.2, 0.1])[0]
            style = random.choice(user_styles)

            prompt = build_generation_prompt(persona, scenario, level, style)
            sample = generate_one(prompt)

            if sample:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                f.flush()
                generated += 1
                print(f"[{generated}/{TOTAL_SAMPLES}] 场景={scenario}, 等级={level}, 风格={style}")
            else:
                print(f"[跳过] 生成无效，重试...")

            time.sleep(0.5)  # 避免请求过于频繁

    print(f"\n完成！共生成 {generated} 条样本，保存至 {OUTPUT_PATH}")

if __name__ == "__main__":
    main()