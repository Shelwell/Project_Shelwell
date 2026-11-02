# desktop/services.py
# 职责：桌面控制台的业务逻辑层（无 UI 依赖）
# 所有函数为同步阻塞，调用方需放入线程池执行

import re
import subprocess
import sys
from pathlib import Path

def _resolve_root() -> Path:
    """打包后 exe 位于项目根的 dist/ShelwellConsole/ 下，向上找含 config/ 的目录"""
    if getattr(sys, "frozen", False):
        cur = Path(sys.executable).resolve().parent
        for _ in range(4):
            if (cur / "config" / "app_config.yaml").exists():
                return cur
            cur = cur.parent
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _resolve_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.runtime_config import get_runtime
from backend.config_loader import get


# Windows 下隐藏子进程控制台窗口（打包为 windowed exe 后必须加）
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def run_ps(cmd: str, timeout: int = 20) -> str:
    """执行 PowerShell 命令，返回合并输出（不弹窗）"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        return f"ERROR: {e}"


def service_state(name: str) -> str:
    out = run_ps(f"(Get-Service -Name '{name}' -ErrorAction SilentlyContinue).Status").strip()
    return out if out and "ERROR" not in out else "NOT_FOUND"


def port_open(port: int) -> bool:
    out = run_ps(
        f"(Test-NetConnection -ComputerName 127.0.0.1 -Port {port} "
        f"-WarningAction SilentlyContinue -InformationLevel Quiet)"
    )
    return "True" in out


def docker_running() -> str:
    out = run_ps('docker inspect --format "{{.State.Running}}" napcat')
    low = out.lower()
    if "cannot find the file" in low or "failed to connect" in low:
        return "DOCKER_OFF"
    if "no such" in low or "error: no such" in low:
        return "NO_CONTAINER"
    if "true" in low:
        return "RUNNING"
    if "false" in low:
        return "STOPPED"
    return "UNKNOWN"


def get_qq_status() -> dict:
    """读取 qq_bot 上报的账号状态文件"""
    import json
    sp = ROOT / "logs" / "qq_status.json"
    if not sp.exists():
        return {"online": False, "error": "状态文件未生成，服务可能未运行"}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except Exception as e:
        return {"online": False, "error": str(e)}


def list_cached_accounts() -> list:
    """
    列出 NapCat 缓存过的账号。
    qq_data 下每个 nt_qq_<hash> 目录对应一个登录过的账号，
    目录名 hash 无法直接还原 QQ 号，需结合 webui.json 的 autoLoginAccount 判断。
    返回 [{"hash": ..., "is_current": bool}]，当前账号从 webui.json 读取。
    """
    import json
    out = []
    qqdir = ROOT / "adapters" / "qq_bot" / "qq_data"
    if not qqdir.exists():
        return out
    current_uid = ""
    wj = ROOT / "adapters" / "qq_bot" / "napcat_data" / "webui.json"
    if wj.exists():
        try:
            current_uid = str(json.loads(wj.read_text(encoding="utf-8")).get("autoLoginAccount", ""))
        except Exception:
            pass
    for d in qqdir.iterdir():
        if d.is_dir() and d.name.startswith("nt_qq_"):
            h = d.name[len("nt_qq_"):]
            out.append({"hash": h, "dir": str(d), "is_current": bool(current_uid)})
    return out


def get_webui_url() -> str:
    """构造带 token 的 WebUI 地址"""
    token = get_napcat_token()
    if token:
        return f"http://127.0.0.1:6099/webui?token={token}"
    return "http://127.0.0.1:6099/webui"


def get_napcat_token() -> str:
    """读取 NapCat WebUI token：优先配置文件的持久值，回退到容器日志"""
    cfg = ROOT / "adapters" / "qq_bot" / "napcat_data" / "webui.json"
    if cfg.exists():
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'"token"\s*:\s*"([^"]+)"', text, re.I)
            if m:
                return m.group(1)
        except Exception:
            pass
    out = run_ps("docker logs --tail 300 napcat 2>&1", 40)
    hits = re.findall(r"WebUi Token:\s*([A-Za-z0-9]+)", out)
    return hits[-1] if hits else ""


def collect_status() -> dict:
    """一次性收集所有状态（供状态页刷新）"""
    return {
        "ShelwellOllama": service_state("ShelwellOllama"),
        "ShelwellQQBot": service_state("ShelwellQQBot"),
        "napcat": docker_running(),
        "port_11434": port_open(11434),
        "port_8765": port_open(8765),
    }


def read_log(name: str, tail: int = 100) -> str:
    lf = ROOT / "logs" / name
    if not lf.exists():
        return f"日志不存在: {lf}"
    try:
        lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-tail:])
    except Exception as e:
        return f"读取失败: {e}"


def list_logs() -> list:
    ldir = ROOT / "logs"
    if not ldir.exists():
        return []
    return sorted([p.name for p in ldir.glob("*.log")])


EMBED_NAME_HINTS = ("embed", "bge-", "gte-", "e5-", "jina-", "minilm")


def _is_embedding_model(name: str) -> bool:
    """判断是否为嵌入模型（不作为对话推理模型展示）"""
    base = name.split(":")[0].lower()
    cfg_emb = (get("model.embedding", "") or "").split(":")[0].lower()
    if cfg_emb and base == cfg_emb:
        return True
    return any(k in base for k in EMBED_NAME_HINTS)


def list_ollama_models() -> list:
    """列出可用于对话的模型，排除嵌入模型"""
    out = run_ps("ollama list", 30)
    models = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if not parts or parts[0].startswith("ERROR"):
            continue
        name = parts[0]
        if _is_embedding_model(name):
            continue
        models.append(name)
    return models


def list_personas() -> list:
    pdir = ROOT / "config" / "persona_prompts"
    return sorted([p.stem for p in pdir.glob("*.yaml") if not p.stem.endswith("_facts")])


API_PRESETS = {
    "custom": {
        "label": "自定义", "base_url": "", "model": "",
        "models": [],
    },
    "openai": {
        "label": "OpenAI", "base_url": "https://api.openai.com", "model": "gpt-4o-mini",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3", "o4-mini"],
    },
    "deepseek": {
        "label": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "moonshot": {
        "label": "Moonshot", "base_url": "https://api.moonshot.cn", "model": "moonshot-v1-8k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "kimi-k2-0905-preview"],
    },
    "qwen": {
        "label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode",
        "model": "qwen-plus",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"],
    },
    "zhipu": {
        "label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long"],
    },
}


def get_presets() -> dict:
    return API_PRESETS


PERSONA_TEMPLATE = """# 人格定义（新人格模板）
# 填写后由 PersonaEngine 编译为系统提示词

name: "名字"
identity:
  name: "名字"
  nickname: null
  age: "24"
  gender: "女"
  self_awareness: "B"   # B 表示人类身份

personality:
  core_traits:
    - 性格关键词一
    - 性格关键词二
  emotional_range:
    - 情绪是真实的反应，不是表演

background:
  origin: "出生地"
  education: "学历"
  key_experiences:
    - 经历一
  values:
    - 价值观一

preferences:
  likes:
    food: ["食物"]
  dislikes:
    food: ["讨厌的食物"]
  habits:
    - 习惯一

speech_style:
  emoji_usage: "禁止使用任何表情符号"
  tone: "语气描述"
  humor: "幽默风格描述"

address_user:
  default: "你"
  forbidden: ["主人", "老板"]

boundaries:
  - 以人类身份自居，不主动提及自己是AI
"""

VALID_NAME = __import__("re").compile(r"^[a-z0-9_]{2,40}$")


def _persona_dir() -> Path:
    return ROOT / "config" / "persona_prompts"


def create_persona(name: str) -> str:
    """创建人格文件，返回路径或错误信息"""
    name = (name or "").strip()
    if not VALID_NAME.match(name):
        return "名称只能用小写字母、数字、下划线，2-40 字符"
    f = _persona_dir() / f"{name}.yaml"
    if f.exists():
        return f"已存在: {name}"
    try:
        f.write_text(PERSONA_TEMPLATE, encoding="utf-8")
        return f"OK:{f}"
    except Exception as e:
        return f"写入失败: {e}"


def delete_persona(name: str) -> str:
    """删除人格文件（当前生效的禁止删除）"""
    name = (name or "").strip()
    if name == get_active().get("persona"):
        return "不能删除当前生效的人格，请先切走"
    f = _persona_dir() / f"{name}.yaml"
    if not f.exists():
        return f"不存在: {name}"
    try:
        f.unlink()
        return "OK"
    except Exception as e:
        return f"删除失败: {e}"


def get_persona_detail(name: str) -> str:
    """读取人格文件原文"""
    f = _persona_dir() / f"{name}.yaml"
    if not f.exists():
        return ""
    try:
        return f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"读取失败: {e}"


def open_in_explorer(path: str) -> str:
    """在资源管理器中定位文件"""
    return run_ps(f'Start-Process explorer.exe -ArgumentList "/select,`"{path}`""')


def list_facts() -> list:
    pdir = ROOT / "config" / "persona_prompts"
    return sorted([p.stem for p in pdir.glob("*_facts.yaml")])


def list_loras() -> list:
    ldir = ROOT / "models" / "lora_adapters"
    if not ldir.exists():
        return []
    return sorted([p.name for p in ldir.iterdir() if p.is_dir()])


def get_active() -> dict:
    return get_runtime().active()


def apply_active(patch: dict) -> dict:
    """更新 active 段"""
    return get_runtime().update({"active": patch})


def apply_generation(patch: dict) -> dict:
    return get_runtime().update({"generation": patch})


def apply_provider_api(base_url: str, api_key: str, model: str, timeout: int,
                       preset: str = "custom", reasoning_effort=None,
                       vision_enabled: bool = False) -> dict:
    return get_runtime().update({"providers": {"openai_compatible": {
        "preset": preset, "base_url": base_url, "api_key": api_key,
        "model": model, "timeout": timeout,
        "reasoning_effort": reasoning_effort,
        "vision_enabled": vision_enabled,
    }}})


def switch_to_ollama() -> dict:
    return get_runtime().update({"active": {"provider": "ollama", "model": get("model.base")}})


def switch_to_api() -> dict:
    rt = get_runtime()
    model = rt.get("providers.openai_compatible.model")
    return get_runtime().update({"active": {"provider": "openai_compatible", "model": model}})


def get_provider_cfg() -> dict:
    return get_runtime().get("providers.openai_compatible", {}) or {}


def get_usage_stats() -> dict:
    """推理用量汇总"""
    from backend.llm.usage import get_tracker
    return get_tracker().snapshot()


def get_usage_recent(n: int = 15) -> list:
    from backend.llm.usage import get_tracker
    return get_tracker().recent(n)


def test_api() -> dict:
    """发一条最小请求测试当前 provider 是否可用"""
    import time
    try:
        from backend.llm import provider
        t0 = time.time()
        text = provider.chat([{"role": "user", "content": "回复ok两个字"}])
        elapsed = round(time.time() - t0, 2)
        from backend.llm.usage import get_tracker
        last = get_tracker().snapshot().get("last") or {}
        return {
            "ok": True,
            "elapsed": elapsed,
            "reply": (text or "")[:100],
            "prompt_tokens": last.get("prompt_tokens", 0),
            "completion_tokens": last.get("completion_tokens", 0),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def estimate_context_usage() -> dict:
    """估算当前人格提示词的上下文占用"""
    try:
        from backend.context_manager import ContextManager
        cm = ContextManager()
        si = cm.system_instruction
        # 粗估：中文约 1 字符 ≈ 0.7 token，英文 1 词 ≈ 1.3 token，取 0.8 折中
        est_tokens = int(len(si) * 0.8)
        return {
            "prompt_chars": len(si),
            "est_tokens": est_tokens,
            "max_ctx": get("generation.num_ctx", 8192),
            "ratio": round(est_tokens / max(get("generation.num_ctx", 8192), 1) * 100, 1),
        }
    except Exception as e:
        return {"error": str(e)}


def get_context_cfg() -> dict:
    return get_runtime().get("context", {}) or {}


def apply_context_cfg(patch: dict) -> dict:
    return get_runtime().update({"context": patch})


def get_generation() -> dict:
    return get_runtime().get("generation", {}) or {}


def _free_ollama_port() -> str:
    """
    清理抢占 11434 的游离 ollama 进程。
    桌面版 Ollama 会自启并占端口，导致 NSSM 服务启动失败。
    返回描述文本。
    """
    out = run_ps(
        "(Get-NetTCPConnection -LocalPort 11434 -State Listen "
        "-ErrorAction SilentlyContinue).OwningProcess", 15)
    pids = [x.strip() for x in out.splitlines() if x.strip().isdigit()]
    if not pids:
        return "11434 端口空闲"
    killed = []
    for pid in pids:
        # 判断父进程：nssm.exe 拉起的是服务，不能杀
        parent = run_ps(
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId", 10).strip()
        pname = run_ps(
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={parent}').Name", 10).strip()
        if "nssm" in pname.lower():
            continue
        run_ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue", 15)
        killed.append(pid)
    if killed:
        import time
        time.sleep(2)
        return "已清理游离 ollama 进程: " + ", ".join(killed)
    return "端口由服务持有"


def start_all_services() -> str:
    """一键启动：Docker Desktop → 三个 Windows 服务 → NapCat 容器"""
    logs = []

    def log(msg):
        logs.append(msg)

    # 1. Docker Desktop
    out = run_ps('docker info 2>&1')
    if "failed to connect" in out.lower() or "cannot find" in out.lower():
        log("[1/3] 启动 Docker Desktop…")
        dd = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
        run_ps(f'Start-Process "{dd}" -ErrorAction SilentlyContinue')
        ok = False
        for i in range(24):
            import time
            time.sleep(5)
            out = run_ps("docker info 2>&1", 20)
            if "failed to connect" not in out.lower():
                ok = True
                log("    Docker 就绪，耗时 %ds" % ((i + 1) * 5))
                break
        if not ok:
            log("    Docker 启动超时，后续步骤可能失败")
    else:
        log("[1/3] Docker 已在运行")

    # 2. Windows 服务
    log("[2/3] 启动 Shelwell 服务…")
    # 先清理抢占 Ollama 端口的游离进程，避免服务启动失败
    port_msg = _free_ollama_port()
    if "已清理" in port_msg:
        log("    " + port_msg)
    for svc in ("ShelwellOllama", "ShelwellQQBot"):
        st = service_state(svc)
        if st.upper() == "RUNNING":
            log(f"    {svc} 已在运行")
        else:
            run_ps(f"Start-Service '{svc}' -ErrorAction SilentlyContinue", 30)
            st2 = service_state(svc)
            log(f"    {svc}: {st} -> {st2}")

    # 3. NapCat 容器
    log("[3/3] 启动 NapCat 容器…")
    running = docker_running()
    if running == "RUNNING":
        log("    NapCat 已在运行")
    elif running == "STOPPED":
        run_ps("docker start napcat", 60)
        log("    已执行 docker start napcat")
    elif running == "DOCKER_OFF":
        log("    Docker 未就绪，跳过 NapCat")
    elif running == "NO_CONTAINER":
        log("    napcat 容器不存在，请先按文档创建")
    else:
        log(f"    NapCat 状态异常: {running}")

    log("")
    log("=== 完成，当前状态 ===")
    for k, v in collect_status().items():
        log(f"  {k}: {v}")
    return "\n".join(logs)


def stop_all_services() -> str:
    """停止全部 Shelwell 服务（Ollama/QQBot 服务 + NapCat 容器，保留 Docker Desktop）"""
    logs = []

    def log(msg):
        logs.append(msg)

    log("[1/2] 停止 Shelwell 服务…")
    for svc in ("ShelwellQQBot", "ShelwellOllama"):
        st = service_state(svc)
        if st.upper() != "RUNNING":
            log(f"    {svc} 未运行（{st}）")
            continue
        run_ps(f"Stop-Service '{svc}' -Force -ErrorAction SilentlyContinue", 30)
        st2 = service_state(svc)
        log(f"    {svc}: {st} -> {st2}")

    log("[2/2] 停止 NapCat 容器…")
    r = docker_running()
    if r == "RUNNING":
        run_ps("docker stop napcat", 90)
        log("    已停止 napcat")
    elif r == "STOPPED":
        log("    napcat 已停止")
    else:
        log(f"    NapCat 状态: {r}")

    log("")
    log("=== 停止完成 ===")
    for k, v in collect_status().items():
        log(f"  {k}: {v}")
    return "\n".join(logs)


def unload_models() -> str:
    """
    手动卸载 Ollama 的生成模型，腾出显存。
    embedding 模型跑在 CPU，不占显存，不用卸载。
    返回执行结果描述。
    """
    import requests
    try:
        rt = get_runtime()
        model = rt.get("active.model") or get("model.base", "")
        if not model:
            return "未配置模型，无需卸载"
        url = f"{get('ollama.url', 'http://localhost:11434')}/api/generate"
        # 空 prompt + keep_alive=0 是 Ollama 的卸载写法
        r = requests.post(url, json={
            "model": model, "keep_alive": 0, "prompt": "",
        }, timeout=30)
        if r.status_code == 200:
            return f"已卸载模型: {model}"
        return f"卸载失败: status={r.status_code} {r.text[:100]}"
    except Exception as e:
        return f"卸载异常: {e}"


def get_loaded_models() -> list:
    """查询当前驻留的模型（含显存占用）"""
    import requests
    try:
        url = f"{get('ollama.url', 'http://localhost:11434')}/api/ps"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return []
        out = []
        for m in r.json().get("models", []):
            out.append({
                "name": m.get("name", ""),
                "size_gb": round(m.get("size", 0) / 1024**3, 2),
                "vram_gb": round(m.get("size_vram", 0) / 1024**3, 2),
                "until": m.get("expires_at", ""),
            })
        return out
    except Exception:
        return []


def get_memory_stats() -> dict:
    """查询记忆库各层条数（供 UI 展示）"""
    try:
        from backend.memory.service import MemoryService
        svc = MemoryService(auto_worker=False)
        st = svc.get_stats()
        return {
            "archive": st["archive"]["total"],
            "vector": st["index"]["vector"],
            "lexical": st["index"]["lexical"],
            "sessions": st["archive"]["sessions"],
        }
    except Exception as e:
        return {"error": str(e)}


def list_episodic_memories(page: int = 1, page_size: int = 20,
                           session_id: str = "", keyword: str = "",
                           date_from: str = "", date_to: str = "") -> dict:
    """
    分页查询情景记忆。
    page 从 1 开始；session_id/keyword/date_from/date_to 为空表示不过滤。
    date 格式 YYYY-MM-DD。
    返回 {"items": [...], "total": n, "page": p, "page_size": size, "pages": total_pages}
    """
    try:
        from backend.memory.archive import Archive
        arc = Archive()
        items = []
        for ev in arc._iter_all():
            # 过滤：会话
            if session_id and ev.session_id != session_id:
                continue
            # 过滤：关键词（在 user_msg 和 ai_msg 里搜）
            if keyword:
                kw = keyword.lower()
                if kw not in (ev.user_msg or "").lower() and kw not in (ev.ai_msg or "").lower():
                    continue
            # 过滤：时间范围
            day = (ev.timestamp or "")[:10]
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue
            items.append(ev)
        # 按时间倒序（新的在前）
        items.sort(key=lambda e: e.timestamp, reverse=True)
        total = len(items)
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        start = (page - 1) * page_size
        page_items = items[start:start + page_size]
        return {
            "items": [{
                "id": e.id,
                "timestamp": e.timestamp,
                "session_id": e.session_id,
                "speaker_id": e.speaker_id,
                "channel_type": e.channel_type,
                "channel_id": e.channel_id,
                "user_msg": e.user_msg,
                "ai_msg": e.ai_msg,
            } for e in page_items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }
    except Exception as e:
        return {"items": [], "total": 0, "page": 1, "page_size": page_size,
                "pages": 1, "error": str(e)}


def get_semantic_stats() -> dict:
    """语义记忆统计"""
    try:
        from backend.memory.semantic import SemanticStore
        st = SemanticStore()
        return st.stats()
    except Exception as e:
        return {"error": str(e)}


def list_semantic_facts(page: int = 1, page_size: int = 20,
                        owner_id: str = "", keyword: str = "") -> dict:
    """分页列语义事实"""
    try:
        from backend.memory.semantic import SemanticStore
        st = SemanticStore()
        r = st.list_facts(page, page_size, owner_id, "", keyword)
        # 转成可序列化 dict
        from backend.memory.forgetting import weight_of
        items = []
        for f in r["items"]:
            items.append({
                "id": f.id, "fact": f.fact,
                "subject": f.subject, "predicate": f.predicate, "object": f.object,
                "confidence": f.confidence, "strength": f.strength,
                "state": f.state, "created_at": f.created_at,
                "source_count": len(f.source_ids),
                "tag": getattr(f, "tag", "misc"),
                "hits": getattr(f, "hits", 0),
                "archived": getattr(f, "archived", False),
                "weight": round(weight_of(f), 2),
            })
        return {"items": items, "total": r["total"], "page": r["page"],
                "page_size": r["page_size"], "pages": r["pages"]}
    except Exception as e:
        return {"items": [], "total": 0, "page": 1, "page_size": page_size,
                "pages": 1, "error": str(e)}


def run_consolidate(session_filter: str = "", limit: int = 50,
                    dry_run: bool = False) -> dict:
    """触发情景→语义巩固"""
    try:
        from backend.memory.consolidator import Consolidator
        c = Consolidator()
        return c.consolidate(session_filter=session_filter or None,
                             limit=limit, dry_run=dry_run)
    except Exception as e:
        return {"error": str(e)}


def delete_semantic_fact(fact_id: str) -> bool:
    try:
        from backend.memory.semantic import SemanticStore
        return SemanticStore().delete(fact_id)
    except Exception:
        return False


def clear_semantic_memory() -> int:
    """只清语义记忆"""
    try:
        from backend.memory.semantic import SemanticStore
        return SemanticStore().clear_all()
    except Exception:
        return 0


def list_episodic_sessions() -> list:
    """列出情景记忆里出现过的会话标识（去重排序）"""
    try:
        from backend.memory.archive import Archive
        arc = Archive()
        sessions = set()
        for ev in arc._iter_all():
            if ev.session_id:
                sessions.add(ev.session_id)
        return sorted(sessions)
    except Exception:
        return []


def clear_all_memory() -> str:
    """
    彻底清空全部记忆：归档 JSONL、向量索引、FTS 索引、任务表。
    返回执行日志。
    """
    import glob
    import os
    logs = []

    def log(m):
        logs.append(m)

    try:
        base = ROOT / "memory"

        # 1. 清空归档 JSONL
        hdir = base / "history"
        n_files = 0
        n_lines = 0
        if hdir.exists():
            for f in hdir.glob("*.jsonl"):
                try:
                    n_lines += sum(1 for _ in f.open(encoding="utf-8"))
                except Exception:
                    pass
                os.remove(f)
                n_files += 1
        log("归档: 删除 %d 个文件, %d 条记录" % (n_files, n_lines))

        # 2. 删除向量 collection
        try:
            import chromadb
            client = chromadb.PersistentClient(
                path=str(base / "vector_store" / "chroma_db"))
            names = [c.name if hasattr(c, "name") else c
                     for c in client.list_collections()]
            removed = []
            for n in names:
                client.delete_collection(n)
                removed.append(n)
            log("向量: 删除 collection %s" % (removed or "无"))
        except Exception as e:
            log("向量清理失败: %s" % e)

        # 3. 删除 FTS 索引文件
        fts = base / "index" / "memory_fts.sqlite3"
        for suffix in ["", "-wal", "-shm"]:
            p = Path(str(fts) + suffix)
            if p.exists():
                os.remove(p)
        log("FTS 索引: 已删除")

        # 4. 清空任务表
        jobs = base / "index" / "jobs.sqlite3"
        for suffix in ["", "-wal", "-shm"]:
            p = Path(str(jobs) + suffix)
            if p.exists():
                os.remove(p)
        log("任务表: 已删除")

        log("")
        log("清空完成")
        return "\n".join(logs)
    except Exception as e:
        log("清空异常: %s" % e)
        return "\n".join(logs)


def restart_service(name: str) -> str:
    return run_ps(f"Restart-Service '{name}' -ErrorAction Stop", 60)


def restart_napcat() -> str:
    return run_ps("docker restart napcat", 90)