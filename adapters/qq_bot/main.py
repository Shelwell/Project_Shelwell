# adapters/qq_bot/main.py
# 职责：QQ 接入层（L5）——NapCat OneBot v11 反向 WebSocket 服务端
# 说明：只做协议转换，业务逻辑交给 backend

import asyncio
import json
import logging
import os
import sys
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import websockets

# 让适配层能 import 到项目 backend
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.orchestrator import Orchestrator
from backend.group_state import GroupStateMachine
from backend.vision import VisionPipeline
from backend.session_summary import summarize_history, configured_model
from engagement import EngagementTracker
from gate import GateRunner
from backend.config_loader import get
from backend.runtime_config import get_runtime

logging.basicConfig(level=logging.INFO, format="[QQ] %(asctime)s %(message)s")
log = logging.getLogger("qq_bot")

HOST = "0.0.0.0"
PORT = 8765
PATH = "/onebot"
MAX_SESSION_MESSAGES = 200   # 每个 session 保留的最大消息条数（窗口由 context.max_length 控制）


class QQBot:
    def __init__(self):
        self.ws = None
        self.self_id = None
        self.sessions = {}       # session_id -> [{"role","content"}]
        self._session_prompt_tokens = {}   # session_id -> 上次 prompt token 数
        self._session_summary = {}         # session_id -> 会话摘要
        self.orchestrator = Orchestrator()
        self._last_silent = False   # 上一轮是否由模型选择沉默（旁白不算她说话）
        self.group_enabled = get("qq_bot.group_enabled", True)
        self.trigger_keywords = get("qq_bot.trigger_keywords", []) or []
        # 自动接话热度状态机
        self.engagement = EngagementTracker(get("qq_bot.auto_engage", {}) or {})
        # 群聊接话门禁：active=正式决策；shadow=只记录
        self.gate = None
        self.bot_name = ""
        try:
            _gate_cfg = (get("qq_bot.group_gate", {})
                         or get("qq_bot.shadow", {}) or {})   # 兼容旧配置名
            if _gate_cfg:
                self.gate = GateRunner(_gate_cfg, str(_PROJECT_ROOT))
                log.info("接话门禁已启用: mode=%s -> %s",
                         self.gate.mode, self.gate.log_path)
        except Exception as e:
            log.warning("接话门禁初始化失败，回退旧策略: %s", e)

        # 最小版群聊状态机：shadow 只记录状态转移，不改变实际发送
        self.state_machine = None
        self.state_log_path = str(_PROJECT_ROOT / "logs" / "group_state.jsonl")
        self._state_tasks = {}
        try:
            _scfg = get("qq_bot.group_state", {}) or {}
            if _scfg.get("enabled", True):
                self.state_machine = GroupStateMachine(_scfg)
                self.state_log_path = str(
                    _PROJECT_ROOT / (_scfg.get("log_path")
                                     or "logs/group_state.jsonl"))
                log.info("群聊状态机已启用: mode=%s -> %s",
                         self.state_machine.mode, self.state_log_path)
                if self.state_machine.mode != "shadow":
                    log.warning("group_state.active 尚未实现，当前仍按 shadow 记录")
        except Exception as e:
            log.warning("群聊状态机初始化失败（不影响发送）: %s", e)

        # 视觉：直连主模型识别小图（QQ 表情包），按指纹缓存去重
        self.vision = None
        try:
            _vcfg = get("vision", {}) or {}
            if _vcfg.get("enabled", True):
                self.vision = VisionPipeline(_vcfg, project_root=str(_PROJECT_ROOT))
                log.info("视觉识别已启用: 最大边长=%s 缓存=%s",
                         self.vision.max_side, self.vision.cache.stats())
        except Exception as e:
            log.warning("视觉模块初始化失败（图片将只记录占位）: %s", e)
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
        for task in list(getattr(self, "_state_tasks", {}).values()):
            try:
                task.cancel()
            except Exception:
                pass
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

        message = evt.get("message")
        text = self.extract_text(message)
        image_segs = self.extract_images(message)
        if not text and not image_segs:
            return

        # 先缓存这条消息，供后续引用
        self._cache_message(evt)

        msg_type = evt.get("message_type")
        user_id = str(evt.get("user_id", ""))
        group_id = str(evt.get("group_id", "")) if msg_type == "group" else user_id
        image_payloads, image_notes = [], []

        # 群聊：先归档（全量存储），再判断是否回复
        need_engage = False
        at_me_flag = False
        if msg_type == "group":
            if not self.group_enabled:
                return
            # @ 或叫名字 = 强信号
            at_me = self.is_at_me(evt, text)
            at_me_flag = at_me
            is_q = self._is_question(text)
            # 观察这条消息，更新热度
            heat = self.engagement.observe(group_id, user_id, text,
                                           is_at_me=at_me, is_question=is_q)
            legacy_engage = at_me or self.engagement.should_engage(
                group_id, is_at_me=at_me)

            # 新策略：门禁决策（active 时直接生效）
            engage = legacy_engage
            reply_id = self.extract_reply(evt.get("message"))
            quoted_sender = ""
            if reply_id and reply_id in self.message_cache:
                quoted_sender = self.message_cache[reply_id].get("sender", "")
            gate_reason, gate_score = "legacy", 0.0
            if self.gate is not None:
                try:
                    rec = self.gate.decide(
                        group_id=group_id, sender_id=user_id,
                        sender_name=self._sender_name(evt), text=text,
                        ts=float(evt.get("time") or 0) or None,
                        at_me=self._is_at_me_explicit(evt),
                        at_other=self._is_at_other(evt),
                        is_question=is_q, reply_to_id=reply_id or "",
                        reply_to_sender=quoted_sender, bot_name=self.bot_name,
                        legacy_engage=legacy_engage,
                        msg_id=str(evt.get("message_id") or ""))
                    d = (rec or {}).get("decision", {})
                    gate_reason = str(d.get("reason", ""))
                    gate_score = float(d.get("score", 0.0) or 0.0)
                    log.info("[Gate:%s] %s reason=%s score=%.2f | %s",
                             self.gate.mode,
                             "接" if d.get("respond") else "不接",
                             d.get("reason", "?"), d.get("score", 0.0), text[:30])
                    if self.gate.enforce:
                        engage = bool(d.get("respond"))
                        if engage != legacy_engage:
                            log.info("[Gate] 与旧策略不同: gate=%s legacy=%s",
                                     engage, legacy_engage)
                except Exception as e:
                    log.warning("接话门禁异常，回退旧策略: %s", e)
                    engage = legacy_engage

            # 状态机 shadow：只记录会怎么排队/合并/冷却，不影响 engage
            reply_to_me = self._is_reply_to_me(reply_id, quoted_sender)
            self._observe_group_state(
                group_id=group_id,
                ts=float(evt.get("time") or 0) or time.time(),
                strong=bool(at_me or reply_to_me),
                weak=bool(is_q or gate_score >= 0.25),
                gate_respond=bool(engage),
                gate_reason=gate_reason,
                score=gate_score,
                sender=self._sender_name(evt),
                text=text,
            )

            if engage:
                need_engage = True
                log.info("接话 [群%s] heat=%.1f at_me=%s", group_id, heat, at_me)
                # 要回复：图片直接随本条消息送进模型（不预先识别，省一次推理）
                image_payloads, image_notes = await self._prepare_images(
                    image_segs, describe_missing=False)
            else:
                # 不接：后台归档（图片识别较慢，不能阻塞后续消息处理）
                self._spawn_archive(evt, text, image_segs,
                                    direct=bool(at_me or reply_to_me))
                return
        else:
            # 私聊：图片随消息送模型
            image_payloads, image_notes = await self._prepare_images(
                image_segs, describe_missing=False)

        session_id = self.session_id(evt)

        # 处理引用：记录被引用内容，供 system 注入（不拼进 history）
        reply_id = self.extract_reply(evt.get("message"))
        quoted = None
        if reply_id:
            # 先查缓存
            if reply_id in self.message_cache:
                quoted = self.message_cache[reply_id]
            else:
                # 缓存未命中：调 API 拉取被引用的消息
                try:
                    msg_data = await self.call_action(
                        "get_msg", {"message_id": reply_id}, timeout=8.0)
                    if msg_data:
                        sender = msg_data.get("sender") or {}
                        quoted = {
                            "sender": (sender.get("card") or sender.get("nickname")
                                       or str(msg_data.get("user_id", ""))),
                            "text": self._plain_text(msg_data.get("message")),
                            "time": msg_data.get("time"),
                        }
                        # 补进缓存
                        self.message_cache[reply_id] = quoted
                        while len(self.message_cache) > self.cache_max:
                            self.message_cache.popitem(last=False)
                        log.info("引用缓存未命中，已从 API 拉取: %s", reply_id)
                except Exception as e:
                    log.warning("拉取引用消息失败: %s", e)

        user_text = text
        if image_notes:
            user_text = (text + " " + " ".join(image_notes)).strip()

        log.info("收到 [%s] %s%s", session_id, user_text[:80],
                 f"（图 {len(image_payloads)} 张）" if image_payloads else "")

        reply = await self.think(session_id, user_text, user_id, msg_type, group_id,
                                 evt, quoted=quoted,
                                 images=[p["b64"] for p in image_payloads],
                                 at_me=at_me_flag)
        # 群聊被动回复：引用用户触发的那条消息本身，不 @ 人
        # （@ 仅用于后续主动找人的功能）
        if msg_type == "group":
            trigger_mid = str(evt.get("message_id") or "")
            final_reply_to = trigger_mid or None
        else:
            final_reply_to = None
        await self.send_reply(evt, reply, reply_to=final_reply_to, at_user=None)
        silent = bool(getattr(self, "_last_silent", False))
        # 记录她自己说了什么（供"回复我"/话题连续性判定）；沉默旁白不算她说话
        if msg_type == "group" and self.gate is not None and not silent:
            try:
                self.gate.on_bot_spoke(group_id, reply)
            except Exception as e:
                log.warning("门禁记录发言失败: %s", e)
        # 接话后热度减半 + 进入冷却（沉默不消耗热度）
        if msg_type == "group" and need_engage and not silent:
            self.engagement.on_engaged(group_id)

    def _cache_message(self, evt):
        """缓存消息内容，供后续引用查询"""
        mid = evt.get("message_id")
        if not mid:
            return
        mid = str(mid)   # 统一字符串，避免与 reply 段的 id 类型不匹配
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

    def extract_images(self, message):
        """取出图片段（QQ 表情包也是 image 段；face 小黄脸没有图，忽略）"""
        if not isinstance(message, list):
            return []
        return [seg.get("data") or {} for seg in message if seg.get("type") == "image"]

    async def _prepare_images(self, segs, describe_missing=False):
        """
        处理本条消息里的图片：
          取字节 → 指纹/尺寸校验 → 缓存命中直接用 → 未命中且需要时才调视觉模型
        返回 (payloads, notes)
          payloads: [{"b64","fingerprint","desc"}]  送给模型
          notes:    ["[表情包：描述]" ...]          进历史/记忆的文字占位
        """
        payloads, notes = [], []
        if not segs:
            return payloads, notes
        if self.vision is None or not self.vision.enabled:
            return payloads, ["[图片]"] * len(segs)

        loop = asyncio.get_event_loop()
        for seg in segs[:max(1, self.vision.max_images)]:
            try:
                raw = await loop.run_in_executor(None, self.vision.load_bytes, seg)
                if not raw:
                    notes.append("[图片]")
                    continue
                info = await loop.run_in_executor(None, self.vision.inspect, raw)
                if not info.get("ok"):
                    w, h = info.get("width"), info.get("height")
                    if info.get("reason") == "too_large" and w:
                        notes.append(f"[图片 {w}x{h}，太大未识别]")
                        log.info("图片跳过：%sx%s 超过 %s", w, h, self.vision.max_side)
                    else:
                        notes.append("[图片]")
                    continue

                desc = self.vision.cached_description(info["fingerprint"])
                if not desc and describe_missing:
                    res = await loop.run_in_executor(None, self.vision.describe, raw)
                    if res.get("ok"):
                        desc = res.get("description") or ""
                        log.info("视觉识别%s: %s",
                                 "（缓存）" if res.get("cached") else "", desc[:40])
                    else:
                        log.info("视觉识别跳过: %s", res.get("reason"))

                payloads.append({"b64": info["b64"],
                                 "fingerprint": info["fingerprint"],
                                 "desc": desc})
                notes.append(f"[表情包：{desc}]" if desc else "[表情包]")
            except Exception as e:
                log.warning("图片处理失败: %s", e)
                notes.append("[图片]")
        return payloads, notes

    def _sender_name(self, evt):
        """发言人显示名：群名片 > 昵称 > QQ 号"""
        sender = evt.get("sender") or {}
        return (sender.get("card") or sender.get("nickname")
                or str(evt.get("user_id", "")))

    def _is_at_me_explicit(self, evt):
        """是否显式 @ 了我（不含关键词，影子模式用）"""
        message = evt.get("message")
        if not isinstance(message, list):
            return False
        self_id = str(evt.get("self_id") or self.self_id or "")
        for seg in message:
            if seg.get("type") == "at":
                qq = str(seg.get("data", {}).get("qq", ""))
                if qq and qq == self_id:
                    return True
        return False

    def _is_at_other(self, evt):
        """是否 @ 了别人（不含 @全体）"""
        message = evt.get("message")
        if not isinstance(message, list):
            return False
        self_id = str(evt.get("self_id") or self.self_id or "")
        for seg in message:
            if seg.get("type") == "at":
                qq = str(seg.get("data", {}).get("qq", ""))
                if qq and qq != self_id and qq != "all":
                    return True
        return False

    def is_at_me(self, evt, text):
        """判断是否是强信号：@我 或 叫名字（关键词）"""
        # @ 检测
        message = evt.get("message")
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq == str(evt.get("self_id", "")):
                        return True
        # 关键词检测
        lower = text.lower()
        for kw in self.trigger_keywords:
            if kw.isascii():
                if kw.lower() in lower:
                    return True
            else:
                if kw in text:
                    return True
        return False

    def _is_question(self, text):
        """判断是否疑问句（热度加权用）"""
        t = (text or "").strip()
        if not t:
            return False
        if t.endswith("?") or t.endswith("？"):
            return True
        return any(k in t for k in ["吗", "呢", "怎么", "为什么", "什么", "哪"])

    def is_triggered(self, evt, text):
        """兼容旧调用：等价于 is_at_me"""
        return self.is_at_me(evt, text)

    def _spawn_archive(self, evt, text, image_segs=None, direct=False):
        """后台归档（含图片识别），不阻塞 WS 消息处理"""
        async def run():
            try:
                await self.archive_only(evt, text, image_segs, direct=direct)
            except Exception as e:
                log.warning("后台归档失败: %s", e)
        try:
            asyncio.get_event_loop().create_task(run())
        except Exception as e:
            log.warning("创建归档任务失败: %s", e)

    async def archive_only(self, evt, text, image_segs=None, direct=False):
        """未触发的群消息：归档（图片先识别成文字描述再入库）"""
        user_id = str(evt.get("user_id", ""))
        group_id = str(evt.get("group_id", ""))
        sender = evt.get("sender") or {}
        speaker_name = sender.get("card") or sender.get("nickname") or user_id
        session_id = f"qqg:{group_id}:{user_id}"

        notes = []
        if image_segs:
            # 未触发回复：仍然识别图片，把描述写进记忆（命中缓存则不重复调用）
            _, notes = await self._prepare_images(image_segs, describe_missing=True)
        user_text = (text + " " + " ".join(notes)).strip() if notes else text
        if not user_text:
            return
        try:
            self.orchestrator.context.archive_turn(
                user_msg=user_text, ai_msg="",
                session_id=session_id,
                speaker_id=user_id, channel_type="group", channel_id=group_id,
                speaker_name=speaker_name, direct=direct,
            )
            if notes:
                log.info("图片已识别入库: %s", " ".join(notes)[:60])
        except Exception as e:
            log.warning("群消息归档失败: %s", e)

    def _is_reply_to_me(self, reply_id, quoted_sender):
        """是否引用/回复了她的消息（状态机强信号）"""
        if not reply_id or not quoted_sender:
            return False
        if self.bot_name and quoted_sender == self.bot_name:
            return True
        return any(k and k in quoted_sender for k in self.trigger_keywords)

    def _observe_group_state(self, group_id, ts, strong, weak,
                             gate_respond, gate_reason, score, sender, text):
        """shadow：只记录状态转移和“会不会接”，不改变实际发送"""
        if self.state_machine is None:
            return
        try:
            rec = self.state_machine.observe(
                group_id=group_id, ts=ts, strong=strong, weak=weak,
                gate_respond=gate_respond, gate_reason=gate_reason,
                score=score, sender=sender, text=text)
            if rec:
                self._log_state(rec)
                if rec.get("pending_until"):
                    self._schedule_state_timer(group_id)
        except Exception as e:
            log.warning("群聊状态机记录失败（不影响发送）: %s", e)

    def _schedule_state_timer(self, group_id):
        if self.state_machine is None:
            return
        old = self._state_tasks.get(group_id)
        if old is not None and not old.done():
            old.cancel()
        task = asyncio.create_task(self._run_state_timer(group_id))
        self._state_tasks[group_id] = task

    async def _run_state_timer(self, group_id):
        """到点推进 shadow 状态：观察超时 / 排队结束 / 冷却结束"""
        try:
            while True:
                deadline = self.state_machine.next_deadline(group_id)
                if not deadline:
                    break
                delay = deadline - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                rec = self.state_machine.finalize(group_id)
                if not rec:
                    break
                self._log_state(rec)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("群聊状态机定时推进失败: %s", e)
        finally:
            task = asyncio.current_task()
            if self._state_tasks.get(group_id) is task:
                self._state_tasks.pop(group_id, None)

    def _log_state(self, rec):
        try:
            rec = dict(rec)
            rec["mode"] = self.state_machine.mode if self.state_machine else "shadow"
            d = os.path.dirname(self.state_log_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.state_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning("写群聊状态日志失败: %s", e)

    def _maybe_compact(self, session_id, history, speaker_name=""):
        """token 探针触发时才压缩；摘要用质量模型，失败则保持原历史"""
        existing = self._session_summary.get(session_id, "")
        try:
            rt = get_runtime()
            num_ctx = int(rt.get("generation.num_ctx")
                          or get("generation.num_ctx", 32768))
            threshold = float(get("context.compact_at", 0.7) or 0.7)
            last = int(self._session_prompt_tokens.get(session_id, 0) or 0)
            keep = int(get("context.keep_recent_turns", 10) or 10) * 2
            if last <= 0 or last < num_ctx * threshold or len(history) <= keep:
                return existing
            old = list(history[:-keep])
            recent = list(history[-keep:])
            summary = summarize_history(
                old, previous_summary=existing,
                max_chars=int(get("context.summary_max_chars", 800) or 800),
                model=configured_model())
            if summary:
                history[:] = recent
                self._session_summary[session_id] = summary
                self._session_prompt_tokens[session_id] = 0
                log.info("会话压缩: %d 条旧消息 -> %d 字摘要 | %s",
                         len(old), len(summary), session_id)
                return summary
        except Exception as e:
            log.warning("会话压缩失败（保持原历史）: %s", e)
        return existing

    def session_id(self, evt):
        if evt.get("message_type") == "private":
            return f"qq:{evt['user_id']}"
        return f"qqg:{evt['group_id']}:{evt['user_id']}"

    async def think(self, session_id, text, speaker_id="", channel_type="private",
                    channel_id="", evt=None, quoted=None, images=None,
                    at_me=False):
        self._last_silent = False
        history = self.sessions.setdefault(session_id, [])
        # history 只存纯文本，发送者信息通过 system 注入（避免模型照抄 [昵称] 格式）
        history.append({"role": "user", "content": text})

        # 构造"谁在说话"的上下文说明，并取出昵称供记忆归属使用
        speaker_hint = ""
        speaker_name = ""
        if evt:
            sender = evt.get("sender") or {}
            speaker_name = (sender.get("card") or sender.get("nickname")
                            or str(speaker_id or ""))
        if channel_type == "group" and evt:
            speaker_hint = f"当前发言者：{speaker_name or speaker_id}"
            if at_me:
                speaker_hint += "（这条在 @ 你，必须给出回应，不要沉默）"

        # 引用说明
        quote_hint = ""
        if quoted:
            quote_hint = f"对方引用了这条消息：{quoted['sender']} 说「{quoted['text'][:100]}」"

        # 模型常驻由配置 generation.keep_alive 控制（不要冷加载），这里不再按热度卸载
        keep_alive = None

        loop = asyncio.get_event_loop()

        def _run():
            summary = self._maybe_compact(session_id, history, speaker_name)
            msgs = list(history)
            if images:
                msgs[-1] = dict(msgs[-1])
                msgs[-1]["images"] = images
            return self.orchestrator.chat(
                msgs, session_id=session_id, current_query=text,
                speaker_id=speaker_id, channel_type=channel_type,
                channel_id=channel_id, speaker_name=speaker_name,
                speaker_hint=speaker_hint, quote_hint=quote_hint,
                keep_alive=keep_alive, session_summary=summary,
            )

        try:
            result = await loop.run_in_executor(None, _run)
        except Exception as e:
            log.warning("orchestrator 异常: %s", e)
            history.pop()
            return "脑子卡了一下\n再说一遍"

        usage = result.get("usage") or {}
        if usage.get("prompt_tokens"):
            self._session_prompt_tokens[session_id] = int(usage["prompt_tokens"])

        if not result.get("ok"):
            log.warning("生成失败: %s", result.get("error"))
            history.pop()
            return "刚走神了\n你再说一次"

        # 模型选择沉默：只发旁白，不把旁白写进对话历史
        if result.get("silent"):
            self._last_silent = True
            log.info("模型选择沉默，仅发送旁白")
            if len(history) > MAX_SESSION_MESSAGES:
                del history[: len(history) - MAX_SESSION_MESSAGES]
            return result["reply"]

        self._last_silent = False
        reply = result["reply"]
        history.append({"role": "assistant", "content": reply})

        # 防内存膨胀：超出上限截断最早的
        if len(history) > MAX_SESSION_MESSAGES:
            del history[: len(history) - MAX_SESSION_MESSAGES]

        return reply

    async def send_reply(self, evt, reply, reply_to=None, at_user=None):
        lines = [l for l in reply.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            # 只在第一条消息上带引用和 @
            rt = reply_to if i == 0 else None
            au = at_user if i == 0 else None
            await self.send_msg(evt, line, reply_to=rt, at_user=au)
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
                self.bot_name = info.get("nickname") or self.bot_name
                log.info("账号信息已更新: %s (%s)",
                         info.get("nickname", "?"), info.get("user_id", "?"))
        except Exception as e:
            log.warning("写状态文件失败: %s", e)

    async def send_msg(self, evt, text, reply_to=None, at_user=None):
        if not self.ws:
            return
        action = "send_private_msg" if evt.get("message_type") == "private" else "send_group_msg"
        # 群聊：需要数组格式才能带 reply / at 段
        if evt.get("message_type") == "group" and (reply_to or at_user):
            message = []
            if reply_to:
                message.append({"type": "reply", "data": {"id": reply_to}})
            if at_user:
                message.append({"type": "at", "data": {"qq": str(at_user)}})
                message.append({"type": "text", "data": {"text": " "}})
            message.append({"type": "text", "data": {"text": text}})
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
