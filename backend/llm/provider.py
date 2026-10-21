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


def chat(messages: list) -> str:
    """
    统一对话入口。按 runtime.active.provider 分发。
    返回模型原始文本；失败抛 ProviderError。
    """
    import time
    rt = get_runtime()
    provider = (rt.get("active.provider") or "ollama").strip()
    model = rt.get("active.model") or ""
    t0 = time.time()
    try:
        if provider == "ollama":
            text = _chat_ollama(messages, rt)
        elif provider == "openai_compatible":
            text = _chat_openai(messages, rt)
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
    )
    return text


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
    return {"temperature": temp, "num_ctx": num_ctx, "num_predict": num_predict}


def _chat_ollama(messages: list, rt) -> str:
    model = rt.get("active.model") or get("model.base", "qwen3.5:9b")
    url = f"{get('ollama.url', 'http://localhost:11434')}/api/chat"
    opts = _gen_options(rt)
    payload = {
        "model": model,
        "messages": messages,
        "think": get("generation.think", False),
        "stream": False,
        "options": {
            "num_ctx": opts["num_ctx"],
            "temperature": opts["temperature"],
            "num_predict": opts["num_predict"],
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=get("ollama.chat_timeout", 300))
    except requests.exceptions.RequestException as e:
        raise ProviderError(f"ollama 连接失败: {e}") from e
    if resp.status_code != 200:
        raise ProviderError(f"ollama status={resp.status_code} detail={resp.text[:200]}")
    return resp.json().get("message", {}).get("content", "")


def _chat_openai(messages: list, rt) -> str:
    cfg = rt.get("providers.openai_compatible", {}) or {}
    base_url = (cfg.get("base_url") or "").rstrip("/")
    api_key = cfg.get("api_key") or ""
    model = cfg.get("model") or rt.get("active.model") or ""
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