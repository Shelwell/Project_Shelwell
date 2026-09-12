import streamlit as st
import requests
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.context_manager import ContextManager
from backend.config_loader import get   # ★ 新增

# ========== 页面配置 ==========
st.set_page_config(
    page_title=get("ui.page_title", "Shelwell"),
    page_icon=get("ui.page_icon", "💬"),
    layout="centered"
)

hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stApp {padding-top: 0px;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ========== 初始化会话状态 ==========
if "messages" not in st.session_state:
    st.session_state.messages = []

if "processing" not in st.session_state:
    st.session_state.processing = False

if "context_manager" not in st.session_state:
    st.session_state.context_manager = ContextManager(
        max_context_length=get("context.max_length", 30),
        memory_enabled=get("context.memory_enabled", True)
    )

# ========== 标题区域 ==========
has_started = any(msg["role"] == "user" for msg in st.session_state.messages)

st.markdown("<h2 style='text-align: center;'>💬 Shelwell</h2>", unsafe_allow_html=True)

if not has_started:
    st.markdown(
        f"<p style='text-align: center; color: #999; font-size: 15px; margin-top: -8px;'>"
        f"{get('ui.welcome_message', '你好呀！')}"
        f"</p>",
        unsafe_allow_html=True
    )

st.divider()

# ========== 显示消息 ==========
# ★ 从配置读取分隔符
BUBBLE_SEP = get("memory.bubble_separator", "\n")

with st.container():
    for msg in st.session_state.messages:
        if msg["role"] == "assistant":
            content = msg["content"]
            # 优先检测 ||| 兼容旧数据，否则按配置的分隔符拆分
            if "|||" in content:
                parts = [p.strip() for p in content.split("|||") if p.strip()]
            else:
                parts = [p.strip() for p in content.split(BUBBLE_SEP) if p.strip()]

            if len(parts) > 1:
                for part in parts:
                    with st.chat_message("assistant"):
                        st.markdown(part)
            else:
                with st.chat_message("assistant"):
                    st.markdown(content)
        else:
            with st.chat_message("user"):
                st.markdown(msg["content"])

# ========== 用户输入 ==========
prompt = st.chat_input("输入消息...")

if prompt and not st.session_state.processing:
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.processing = True
    st.rerun()

# ========== 处理 AI 回复 ==========
if st.session_state.processing:
    context_mgr = st.session_state.context_manager

    current_query = None
    for msg in reversed(st.session_state.messages):
        if msg["role"] == "user":
            current_query = msg["content"]
            break

    try:
        recent_messages = context_mgr.prepare_context(
            st.session_state.messages,
            current_query=current_query
        )
    except Exception as e:
        print(f"[Context] 记忆检索失败，降级为纯上下文: {e}")
        recent_messages = st.session_state.messages[-get("context.max_length", 30):]

    try:
        stats = context_mgr.get_stats(st.session_state.messages)
        print(f"[Context] 消息: {stats['context_used']}/{stats['max_allowed']} | "
              f"记忆库总数: {stats['total_memories']}")
    except Exception:
        pass

    # ★ 从配置读取模型名、URL、推理参数
    try:
        response = requests.post(
            f"{get('ollama.url', 'http://localhost:11434')}/api/chat",
            json={
                "model": get("model.base", "qwen3.5:9b"),
                "messages": recent_messages,
                "think": get("generation.think", False),
                "stream": False,
                "options": {
                    "num_ctx": get("generation.num_ctx", 8192),
                    "temperature": get("generation.temperature", 0.7),
                    "num_predict": get("generation.num_predict", 1024)
                }
            },
            timeout=get("ollama.chat_timeout", 300)
        )

        if response.status_code == 200:
            result = response.json()
            assistant_reply = result.get("message", {}).get("content", "").strip()
            if not assistant_reply:
                assistant_reply = "（Shelwell 似乎走神了，能再说一遍吗？）"
                print("[Warning] Ollama 返回空回复")
        else:
            assistant_reply = f"⚠️ 状态码 {response.status_code}，请检查Ollama服务。"

    except requests.exceptions.ConnectionError:
        assistant_reply = "⚠️ 无法连接Ollama，请确认服务已启动。"
    except requests.exceptions.ReadTimeout:
        assistant_reply = "⚠️ 回复生成时间过长，请稍后重试或简化问题。"
    except Exception as e:
        assistant_reply = f"⚠️ 发生错误：{str(e)}"

    st.session_state.messages.append({"role": "assistant", "content": assistant_reply})

    if current_query and assistant_reply and not assistant_reply.startswith("⚠️"):
        try:
            context_mgr.archive_turn(
                user_msg=current_query,
                ai_msg=assistant_reply,
                session_id="default"
            )
        except Exception as e:
            print(f"[Memory] 归档失败（不影响对话）: {e}")

    st.session_state.processing = False
    st.rerun()