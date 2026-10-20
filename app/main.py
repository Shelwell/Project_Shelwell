import streamlit as st
import requests
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.orchestrator import Orchestrator
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

if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = Orchestrator()

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
    orch = st.session_state.orchestrator

    current_query = None
    for msg in reversed(st.session_state.messages):
        if msg["role"] == "user":
            current_query = msg["content"]
            break

    result = orch.chat(
        st.session_state.messages,
        session_id="default",
        current_query=current_query,
    )

    if result["ok"]:
        assistant_reply = result["reply"]
    else:
        err = result.get("error") or "unknown"
        if "Connection" in err or "11434" in err:
            assistant_reply = "无法连接Ollama，请确认服务已启动。"
        elif "Timeout" in err or "timeout" in err.lower():
            assistant_reply = "回复生成时间过长，请稍后重试。"
        else:
            assistant_reply = f"发生错误：{err}"

    st.session_state.messages.append({"role": "assistant", "content": assistant_reply})
    st.session_state.processing = False
    st.rerun()
