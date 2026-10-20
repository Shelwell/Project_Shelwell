# adapters/qq_bot/main.py
# 职责：QQ 接入层（L5）——NapCat OneBot v11 反向 WebSocket 服务端
# 说明：只做协议转换，业务逻辑交给 backend

import asyncio
import json
import logging
import sys
from pathlib import Path

import websockets

# 让适配层能 import 到项目 backend
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO, format="[QQ] %(asctime)s %(message)s")
log = logging.getLogger("qq_bot")

HOST = "0.0.0.0"
PORT = 8765
PATH = "/onebot"
MAX_SESSION_MESSAGES = 40   # 每个 session 保留的最大消息条数（约 20 轮）


class QQBot:
    def __init__(self):
        self.ws = None
        self.self_id = None
        self.sessions = {}       # session_id -> [{"role","content"}]
        self.orchestrator = Orchestrator()

    async def handle(self, ws):
        self.ws = ws
        try:
            path = ws.request.path
        except Exception:
            path = "?"
        log.info("NapCat 已连接 %s", path)
        try:
            async for raw in ws:
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                await self.on_event(evt)
        except websockets.ConnectionClosed:
            log.info("NapCat 断开")
        finally:
            self.ws = None

    async def on_event(self, evt):
        pt = evt.get("post_type")
        if pt == "meta_event":
            self.self_id = evt.get("self_id")
            return
        if pt != "message":
            return
        if evt.get("message_type") not in ("private", "group"):
            return
        if evt.get("user_id") == evt.get("self_id"):
            return

        text = self.extract_text(evt.get("message"))
        if not text:
            return
        session_id = self.session_id(evt)
        log.info("收到 [%s] %s", session_id, text)

        reply = await self.think(session_id, text)
        await self.send_reply(evt, reply)

    def extract_text(self, message):
        if isinstance(message, str):
            return message.strip()
        if isinstance(message, list):
            parts = [seg.get("data", {}).get("text", "") for seg in message if seg.get("type") == "text"]
            return "".join(parts).strip()
        return ""

    def session_id(self, evt):
        if evt.get("message_type") == "private":
            return f"qq:{evt['user_id']}"
        return f"qqg:{evt['group_id']}:{evt['user_id']}"

    async def think(self, session_id, text):
        history = self.sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": text})

        # orchestrator 内部是同步 requests，放线程池避免阻塞事件循环
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.orchestrator.chat(
                    list(history), session_id=session_id, current_query=text
                ),
            )
        except Exception as e:
            log.warning("orchestrator 异常: %s", e)
            history.pop()
            return "脑子卡了一下\n再说一遍"

        if not result.get("ok"):
            log.warning("生成失败: %s", result.get("error"))
            history.pop()
            return "刚走神了\n你再说一次"

        reply = result["reply"]
        history.append({"role": "assistant", "content": reply})

        # 防内存膨胀：超出上限截断最早的
        if len(history) > MAX_SESSION_MESSAGES:
            del history[: len(history) - MAX_SESSION_MESSAGES]

        return reply

    async def send_reply(self, evt, reply):
        lines = [l for l in reply.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            await self.send_msg(evt, line)
            if i < len(lines) - 1:
                await asyncio.sleep(0.8)

    async def send_msg(self, evt, text):
        if not self.ws:
            return
        action = "send_private_msg" if evt.get("message_type") == "private" else "send_group_msg"
        params = {"message": text}
        if evt.get("message_type") == "private":
            params["user_id"] = evt["user_id"]
        else:
            params["group_id"] = evt["group_id"]
        payload = {"action": action, "params": params, "echo": f"send_{asyncio.get_event_loop().time()}"}
        try:
            await self.ws.send(json.dumps(payload, ensure_ascii=False))
            log.info("已发送 -> %s: %s", params.get("user_id") or params.get("group_id"), text)
        except Exception as e:
            log.warning("发送失败: %s", e)


async def main():
    bot = QQBot()
    async with websockets.serve(bot.handle, HOST, PORT, ping_interval=None):
        log.info("监听 ws://%s:%d%s", HOST, PORT, PATH)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())