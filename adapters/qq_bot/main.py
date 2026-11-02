# adapters/qq_bot/main.py
# 职责：QQ 接入层（L5）——NapCat OneBot v11 反向 WebSocket 服务端
# 说明：只做协议转换，业务逻辑交给 backend

import asyncio
import json
import logging
import os
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import websockets

# 让适配层能 import 到项目 backend
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.orchestrator import Orchestrator
from backend.config_loader import get

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
        self.group_enabled = get("qq_bot.group_enabled", True)
        self.trigger_keywords = get("qq_bot.trigger_keywords", []) or []
        # 消息缓存：message_id -> {sender, text, time}，用于引用回复
        self.message_cache = OrderedDict()
        self.cache_max = 100
        # action 请求-响应：echo -> Future
        self.pending_actions = {}
        self._echo_seq = 0
        # 账号状态文件（供 desktop 读取）
        self._status_path = str(_PROJECT_ROOT / "logs" / "qq_status.json")
        log.info("群聊开关: %s | 触发词: %s", self.group_enabled, self.trigger_keywords)

    def shutdown(self):
        """释放机器人持有的资源"""
        if self.orchestrator is not None and hasattr(self.orchestrator, "shutdown"):
            try:
                self.orchestrator.shutdown()
            except Exception as e:
                log.warning("关闭 orchestrator 失败: %s", e)

    async def handle(self, ws):
        self.ws = ws
        try:
            path = ws.request.path
        except Exception:
            path = "?"
        log.info("NapCat 已连接 %s", path)
        # 连接建立后拉取账号信息
        asyncio.create_task(self._delayed_refresh())
        try:
            async for raw in ws:
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                # api 响应（带 echo 且在 pending 里）优先路由
                echo = evt.get("echo")
                if echo and echo in self.pending_actions:
                    fut = self.pending_actions.pop(echo)
                    if not fut.done():
                        fut.set_result(evt)
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
            mt = evt.get("meta_event_type")
            # 心跳时检查是否需要刷新账号状态（10 分钟一次）
            if mt == "heartbeat":
                now = asyncio.get_event_loop().time()
                if now - getattr(self, "_last_refresh", 0) > 600:
                    asyncio.create_task(self.refresh_account_info())
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

        # 先缓存这条消息，供后续引用
        self._cache_message(evt)

        msg_type = evt.get("message_type")
        user_id = str(evt.get("user_id", ""))
        group_id = str(evt.get("group_id", "")) if msg_type == "group" else user_id

        # 群聊：先归档（全量存储），再判断是否触发回复
        if msg_type == "group":
            if not self.group_enabled:
                return
            triggered = self.is_triggered(evt, text)
            if not triggered:
                # 未触发：只归档，不回复
                self.archive_only(evt, text)
                return

        session_id = self.session_id(evt)

        # 处理引用：被引用的消息内容拼到开头
        reply_id = self.extract_reply(evt.get("message"))
        quoted = None
        if reply_id and reply_id in self.message_cache:
            q = self.message_cache[reply_id]
            quoted = q
            text = f"[引用 {q['sender']}: {q['text']}]\n{text}"

        log.info("收到 [%s] %s", session_id, text)

        reply = await self.think(session_id, text, user_id, msg_type, group_id, evt)
        await self.send_reply(evt, reply, reply_to=reply_id or None)

    def _cache_message(self, evt):
        """缓存消息内容，供后续引用查询"""
        mid = evt.get("message_id")
        if not mid:
            return
        sender = evt.get("sender") or {}
        self.message_cache[mid] = {
            "sender": sender.get("card") or sender.get("nickname") or str(evt.get("user_id", "")),
            "text": self._plain_text(evt.get("message")),
            "time": evt.get("time"),
        }
        # FIFO 淘汰
        while len(self.message_cache) > self.cache_max:
            self.message_cache.popitem(last=False)

    def _plain_text(self, message):
        """只提取文本段，不处理引用"""
        if isinstance(message, str):
            return message.strip()
        if isinstance(message, list):
            parts = [seg.get("data", {}).get("text", "") for seg in message if seg.get("type") == "text"]
            return "".join(parts).strip()
        return ""

    def extract_reply(self, message):
        """提取被引用的消息ID，无引用返回空"""
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "reply":
                    return str(seg.get("data", {}).get("id", ""))
        return ""

    def extract_text(self, message):
        if isinstance(message, str):
            return message.strip()
        if isinstance(message, list):
            parts = [seg.get("data", {}).get("text", "") for seg in message if seg.get("type") == "text"]
            return "".join(parts).strip()
        return ""

    def is_triggered(self, evt, text):
        """群聊判断是否触发响应：@机器人 或 命中关键词"""
        # @ 检测：message 数组里有 at 类型的段且指向自己
        message = evt.get("message")
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq == str(evt.get("self_id", "")):
                        return True
        # 关键词检测（英文忽略大小写，中文子串）
        lower = text.lower()
        for kw in self.trigger_keywords:
            if kw.isascii():
                if kw.lower() in lower:
                    return True
            else:
                if kw in text:
                    return True
        return False

    def archive_only(self, evt, text):
        """未触发的群消息：只归档不回复"""
        user_id = str(evt.get("user_id", ""))
        group_id = str(evt.get("group_id", ""))
        session_id = f"qqg:{group_id}:{user_id}"
        try:
            self.orchestrator.context.archive_turn(
                user_msg=text, ai_msg="",
                session_id=session_id,
                speaker_id=user_id, channel_type="group", channel_id=group_id,
            )
        except Exception as e:
            log.warning("群消息归档失败: %s", e)

    def session_id(self, evt):
        if evt.get("message_type") == "private":
            return f"qq:{evt['user_id']}"
        return f"qqg:{evt['group_id']}:{evt['user_id']}"

    async def think(self, session_id, text, speaker_id="", channel_type="private",
                    channel_id="", evt=None):
        history = self.sessions.setdefault(session_id, [])
        # 群聊里带发言者昵称，让模型知道谁在说话
        display = text
        if channel_type == "group" and evt:
            card = (evt.get("sender") or {}).get("card") or (evt.get("sender") or {}).get("nickname") or speaker_id
            display = f"[{card}] {text}"
        history.append({"role": "user", "content": display})

        # orchestrator 内部是同步 requests，放线程池避免阻塞事件循环
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.orchestrator.chat(
                    list(history), session_id=session_id, current_query=display,
                    speaker_id=speaker_id, channel_type=channel_type,
                    channel_id=channel_id,
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

    async def send_reply(self, evt, reply, reply_to=None):
        lines = [l for l in reply.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            # 只在第一条消息上带引用
            rt = reply_to if i == 0 else None
            await self.send_msg(evt, line, reply_to=rt)
            if i < len(lines) - 1:
                await asyncio.sleep(0.8)

    async def call_action(self, action: str, params: dict = None, timeout: float = 10.0):
        """通过 WS 发 action 请求并等响应，返回 data 字段；失败返回 None"""
        if not self.ws:
            return None
        self._echo_seq += 1
        echo = f"act_{self._echo_seq}_{int(asyncio.get_event_loop().time() * 1000)}"
        fut = asyncio.get_event_loop().create_future()
        self.pending_actions[echo] = fut
        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            await self.ws.send(json.dumps(payload, ensure_ascii=False))
            result = await asyncio.wait_for(fut, timeout=timeout)
            if result.get("status") == "ok" or result.get("retcode") == 0:
                return result.get("data", {})
            log.warning("action %s 失败: %s", action, result)
            return None
        except asyncio.TimeoutError:
            self.pending_actions.pop(echo, None)
            log.warning("action %s 超时", action)
            return None
        except Exception as e:
            self.pending_actions.pop(echo, None)
            log.warning("action %s 异常: %s", action, e)
            return None

    async def get_login_info(self):
        """查询当前登录账号信息"""
        return await self.call_action("get_login_info")

    async def get_status(self):
        """查询 NapCat 运行状态"""
        return await self.call_action("get_status")

    async def _delayed_refresh(self):
        """连接后延迟 2 秒拉取，避免和握手撞车"""
        await asyncio.sleep(2)
        await self.refresh_account_info()

    async def refresh_account_info(self):
        """拉取账号信息写入状态文件，供 desktop 读取"""
        self._last_refresh = asyncio.get_event_loop().time()
        info = await self.get_login_info()
        status = await self.get_status()
        data = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "online": bool(info),
            "login": info or {},
            "status": status or {},
        }
        try:
            os.makedirs(os.path.dirname(self._status_path), exist_ok=True)
            import json as _json
            with open(self._status_path, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
            if info:
                log.info("账号信息已更新: %s (%s)",
                         info.get("nickname", "?"), info.get("user_id", "?"))
        except Exception as e:
            log.warning("写状态文件失败: %s", e)

    async def send_msg(self, evt, text, reply_to=None):
        if not self.ws:
            return
        action = "send_private_msg" if evt.get("message_type") == "private" else "send_group_msg"
        # 群聊带引用时用数组格式
        if reply_to and evt.get("message_type") == "group":
            message = [
                {"type": "reply", "data": {"id": reply_to}},
                {"type": "text", "data": {"text": text}},
            ]
        else:
            message = text
        params = {"message": message}
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
    try:
        async with websockets.serve(bot.handle, HOST, PORT, ping_interval=None):
            log.info("监听 ws://%s:%d%s", HOST, PORT, PATH)
            await asyncio.Future()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("收到停止信号")
    finally:
        bot.shutdown()
        log.info("资源已释放")


if __name__ == "__main__":
    asyncio.run(main())