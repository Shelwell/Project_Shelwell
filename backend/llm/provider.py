# backend/llm/provider.py
# 职责：L2 模型层——统一推理接口，屏蔽 Ollama / OpenAI 兼容 API 差异

import requests

from backend.config_loader import get
from backend.runtime_config import get_runtime
from backend.llm.usage import get_tracker


_LAST_USAGE = {"data": {}}


class ProviderError(RuntimeError):
    """推理调用失败"""
    pass


def chat(messages: list, keep_alive: str = None, model_override: str = None) -> str:
    """
    统一对话入口。按 runtime.active.provider 分发。
    keep_alive: Ollama 模型驻留时长（如 "30m"/"5m"），None 时用配置默认值。
    model_override: 临时指定模型（如语义提炼用的小模型），None 时用 active.model。
    返回模型原始文本；失败抛 ProviderError。
    """
    import time
    rt = get_runtime()
    provider = (rt.get("active.provider") or "ollama").strip()
    model = model_override or rt.get("active.model") or ""
    t0 = time.time()
    _LAST_USAGE["data"] = {}
    try:
        if provider == "ollama":
            text = _chat_ollama(messages, rt, keep_alive, model_override)
        elif provider == "openai_compatible":
            text = _chat_openai(messages, rt, model_override)
        else:
            raise ProviderError(f"未知 provider: {provider}")
    except Exception as e:
        get_tracker().record(provider, model, False, elapsed=time.time() - t0, error=str(e))
        raise
    usage = _LAST_USAGE.get("data") or {}
    get_tracker().record(
        provider, model, True,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        elapsed=time.time() - t0,
        prompt_eval_duration=usage.get("prompt_eval_duration", 0),
        eval_duration=usage.get("eval_duration", 0),
    )
    return text


def get_last_usage() -> dict:
    """最近一次调用的 token/耗时元数据（供 token 探针使用）"""
    return dict(_LAST_USAGE.get("data") or {})


def _gen_options(rt) -> dict:
    """生成参数：runtime 覆盖 app_config"""
    temp = rt.get("generation.temperature")
    if temp is None:
        temp = get("generation.temperature", 0.7)
    num_ctx = rt.get("generation.num_ctx")
    if num_ctx is None:
        num_ctx = get("generation.num_ctx", 8192)
    num_predict = rt.get("generation.num_predict")
    if num_predict is None:
        num_predict = get("generation.num_predict", 1024)
    opts = {"temperature": temp, "num_ctx": num_ctx, "num_predict": num_predict}

    # 可选参数：只在显式配置时下发，避免用默认值改变现有行为
    top_p = rt.get("generation.top_p")
    if top_p is None:
        top_p = get("generation.top_p", None)
    if top_p is not None:
        opts["top_p"] = float(top_p)
    repeat = rt.get("generation.repeat_penalty")
    if repeat is None:
        repeat = get("generation.repeat_penalty", None)
    if repeat is not None:
        opts["repeat_penalty"] = float(repeat)
    return opts


def _runtime_value(rt, key: str, default):
    """runtime 优先，回退 app_config"""
    v = rt.get(key)
    if v is None:
        return get(key, default)
    return v


def _chat_ollama(messages: list, rt, keep_alive: str = None,
                 model_override: str = None) -> str:
    model = model_override or rt.get("active.model") or get("model.base", "qwen3.5:9b")
    url = f"{get('ollama.url', 'http://localhost:11434')}/api/chat"
    opts = _gen_options(rt)
    # 调用方未指定时：runtime 的 keep_alive 优先，其次 app_config
    if keep_alive is None:
        keep_alive = _runtime_value(rt, "generation.keep_alive",
                                    get("generation.keep_alive", "10m"))
    payload = {
        "model": model,
        "messages": messages,
        "think": bool(_runtime_value(rt, "generation.think",
                                     get("generation.think", False))),
        "stream": False,
        "keep_alive": keep_alive,
        "options": opts,
    }
    try:
        resp = requests.post(url, json=payload, timeout=get("ollama.chat_timeout", 300))
    except requests.exceptions.RequestException as e:
        raise ProviderError(f"ollama 连接失败: {e}") from e
    if resp.status_code != 200:
        raise ProviderError(f"ollama status={resp.status_code} detail={resp.text[:200]}")
    data = resp.json()
    _LAST_USAGE["data"] = {
        "prompt_tokens": int(data.get("prompt_eval_count", 0) or 0),
        "completion_tokens": int(data.get("eval_count", 0) or 0),
        "prompt_eval_duration": int(data.get("prompt_eval_duration", 0) or 0),
        "eval_duration": int(data.get("eval_duration", 0) or 0),
    }
    return data.get("message", {}).get("content", "")


def _chat_openai(messages: list, rt, model_override: str = None) -> str:
    cfg = rt.get("providers.openai_compatible", {}) or {}
    base_url = (cfg.get("base_url") or "").rstrip("/")
    api_key = cfg.get("api_key") or ""
    model = model_override or cfg.get("model") or rt.get("active.model") or ""
    timeout = cfg.get("timeout") or 120
    if not base_url or not model:
        raise ProviderError("openai_compatible 未配置 base_url 或 model")

    opts = _gen_options(rt)
    url = f"{base_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": opts["temperature"],
        "max_tokens": opts["num_predict"],
        "stream": False,
    }
    effort = cfg.get("reasoning_effort")
    if effort:
        payload["reasoning_effort"] = effort
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise ProviderError(f"API 连接失败: {e}") from e
    if resp.status_code != 200:
        raise ProviderError(f"API status={resp.status_code} detail={resp.text[:200]}")
    data = resp.json()
    _LAST_USAGE["data"] = data.get("usage") or {}
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ProviderError(f"API 返回格式异常: {str(data)[:200]}") from e