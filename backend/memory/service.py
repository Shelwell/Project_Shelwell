# backend/memory/service.py
# 职责：L3 记忆系统的聚合入口（阶段 A：可靠情景记忆）
# 兼容：对外暴露 add_memory / search / get_stats，签名与旧 MemoryRetriever 一致
# 当前阶段：仅实现情景记忆；语义事实、RRF、遗忘在后续阶段接入

import threading
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.config_loader import get
from backend.memory.archive import Archive
from backend.memory.indexer import Indexer, seg, seg_query
from backend.memory.jobs import JobQueue
from backend.memory.models import MemoryEvent


def _is_question_like(text: str) -> bool:
    """判断用户消息是否为问句或元问题（问句字面相似度高但信息价值低）"""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("?") or t.endswith("？"):
        return True
    # 元问题关键词：问记忆、问刚才、问是否记得
    meta_kw = ["记得", "刚才", "我说", "我说过", "什么来着", "我说啥"]
    if any(k in t for k in meta_kw):
        return True
    # 常见疑问词结尾
    if any(t.endswith(k) for k in ["吗", "呢", "么"]):
        return True
    return False


def _dedupe_by_user_msg(results: dict) -> dict:
    """同一 user_msg 只保留相似度最高的一条（后写入的优先）"""
    seen = {}
    for eid, r in results.items():
        key = (r.get("metadata", {}).get("user_msg", "") or r["content"])[:60]
        prev = seen.get(key)
        if prev is None or r["similarity"] >= prev[1]["similarity"]:
            seen[key] = (eid, r)
    return {eid: r for eid, r in seen.values()}


class MemoryService:
    """记忆服务聚合入口"""

    def __init__(self, auto_worker: bool = False,
                 archive_dir: Optional[str] = None,
                 chroma_dir: Optional[str] = None,
                 fts_db: Optional[str] = None,
                 jobs_db: Optional[str] = None):
        self.archive = Archive(history_dir=archive_dir)
        self.indexer = Indexer(chroma_dir=chroma_dir, fts_db=fts_db)
        self.jobs = JobQueue(db_path=jobs_db)
        # 关系等级：只按归档消息计数，模型无权参与判定
        self.relationship = self._init_relationship(archive_dir)
        # 语义记忆（懒加载，失败降级为无语义）
        self._semantic = None
        self._semantic_dir = chroma_dir
        try:
            from backend.memory.semantic import SemanticStore
            self._semantic = SemanticStore(chroma_dir=chroma_dir)
        except Exception as e:
            print(f"[Memory] 语义存储初始化失败，降级: {e}")
        self._worker_thread = None
        self._stop_flag = threading.Event()
        # 自动提炼：标记文件记录“今天是否已经跑过”
        base = Path(self.archive.history_dir).parent
        self._auto_marker = base / "index" / "last_auto_consolidate.txt"
        self._auto_running = False
        # 进程启动时恢复上次崩溃遗留的 running 任务
        self.jobs.recover_stale()
        if auto_worker:
            self.start_worker()

    def _init_relationship(self, archive_dir):
        """关系等级存储：默认路径读配置；测试用临时归档时放到同级 index 下"""
        try:
            if not get("relationship.enabled", True):
                return None
            from backend.relationship import RelationshipStore
            if archive_dir is None:
                store = RelationshipStore()
                # 首次建库从归档回填，升级后不会把老朋友当陌生人
                store.ensure_backfill(self.archive)
                return store
            db = Path(self.archive.history_dir).parent / "index" / "relationships.sqlite3"
            return RelationshipStore(db_path=str(db))
        except Exception as e:
            print(f"[Memory] 关系等级存储初始化失败，降级: {e}")
            return None

    # ============ 对外接口（兼容旧 MemoryRetriever） ============

    def add_memory(self, user_msg: str, ai_msg: str, session_id: str = "default",
                   score: float = 0.0, emotion: str = "",
                   speaker_id: str = "", channel_type: str = "",
                   channel_id: str = "", speaker_name: str = "") -> bool:
        """
        写入一轮对话：
        1. 归档到 JSONL（权威）
        2. 提交索引任务（异步）
        评分暂只记录，不做过滤（阶段 A 策略）。
        """
        event = MemoryEvent.create(
            session_id, user_msg, ai_msg, score=score, emotion=emotion,
            speaker_id=speaker_id, channel_type=channel_type or "private",
            channel_id=channel_id, speaker_name=speaker_name,
        )
        if not self.archive.append(event):
            return False
        # 关系等级：按归档消息累加条数与首次出现时间（失败不影响记忆）
        if self.relationship is not None:
            try:
                self.relationship.observe(
                    speaker_id=event.speaker_id,
                    display_name=event.speaker_name,
                    channel_type=event.channel_type,
                    channel_id=event.channel_id)
            except Exception as e:
                print(f"[Memory] 关系等级更新失败: {e}")
        # 同步索引，保证写入后立即可查；失败才入队异步重试
        try:
            r = self.indexer.index(event)
            if r["vector"] or r["lexical"]:
                return True
        except Exception as e:
            print(f"[Memory] 同步索引失败，转异步重试: {e}")
        self.jobs.enqueue("index", {"event_id": event.id}, dedup_key=event.id)
        return True

    def search(self, query: str, top_k: int = None, min_similarity: float = None,
               session_id: str = None, speaker_id: str = None,
               channel_type: str = None, channel_id: str = None) -> list:
        """
        检索情景记忆。
        阶段 A：向量 + 关键词合并（简化版，取并集按相似度/命中排序）。
        阶段 C 会替换为 RRF。
        返回：[{"content", "metadata", "similarity", "source"}]
        """
        if top_k is None:
            top_k = get("memory.top_k", 3)
        if min_similarity is None:
            min_similarity = get("memory.min_similarity", 0.35)

        results = {}

        # 1. 向量检索
        vec = self.indexer.embedder.embed_safe(query)
        if vec and self.indexer.collection.count() > 0:
            try:
                total = self.indexer.collection.count()
                n = min(top_k * 3, total)
                # 私聊场景：分两层查，私聊记忆优先，群聊记忆补充但降权
                if channel_type == "private" and speaker_id:
                    # 第一层：只看 private
                    try:
                        qres = self.indexer.collection.query(
                            query_embeddings=[vec], n_results=n,
                            where={"$and": [{"speaker_id": speaker_id},
                                            {"channel_type": "private"}]},
                        )
                        _collect_results(qres, results, min_similarity, "vector", 1.0)
                    except Exception:
                        pass
                    # 第二层：补充 group（降权 0.7）
                    try:
                        qres2 = self.indexer.collection.query(
                            query_embeddings=[vec], n_results=n,
                            where={"$and": [{"speaker_id": speaker_id},
                                            {"channel_type": "group"}]},
                        )
                        _collect_results(qres2, results, min_similarity, "vector", 0.7)
                    except Exception:
                        pass
                else:
                    # 群聊或其他场景：按原逻辑
                    where = _build_where(session_id, speaker_id, channel_type, channel_id)
                    qres = self.indexer.collection.query(
                        query_embeddings=[vec], n_results=n, where=where)
                    _collect_results(qres, results, min_similarity, "vector", 1.0)
            except Exception as e:
                print(f"[Memory] 向量检索失败: {e}")

        # 2. 关键词检索（补充向量漏掉的精确词）
        try:
            q = seg_query(query)
            if q:
                with self.indexer._connect_fts() as con:
                    rows = con.execute(
                        "SELECT id FROM fts_episodic WHERE fts_episodic MATCH ? LIMIT ?",
                        (q, top_k * 2)).fetchall()
            else:
                rows = []
            for r in rows:
                eid = r["id"]
                if eid in results:
                    continue
                ev = self.archive.get(eid)
                if not ev:
                    continue
                if not _event_in_scope(ev, session_id, speaker_id, channel_type, channel_id):
                    continue
                results[eid] = {"content": ev.combined_text,
                                "metadata": {"timestamp": ev.timestamp,
                                             "session_id": ev.session_id,
                                             "speaker_id": ev.speaker_id,
                                             "channel_type": ev.channel_type,
                                             "channel_id": ev.channel_id,
                                             "user_msg": (ev.user_msg or "")[:200]},
                                "similarity": 0.5, "source": "lexical"}
        except Exception as e:
            print(f"[Memory] 关键词检索失败: {e}")

        # 3. 去重：同一 user_msg 只留一条
        results = _dedupe_by_user_msg(results)

        # 4. 问句降权：问句字面相似度高但信息价值低，避免挤掉真正的事实
        for r in results.values():
            user_text = r.get("metadata", {}).get("user_msg", "")
            if _is_question_like(user_text):
                r["similarity"] = round(r["similarity"] * 0.5, 3)

        # 5. 排序取 Top-K
        ordered = sorted(results.values(), key=lambda x: x["similarity"], reverse=True)
        return ordered[:top_k]

    def get_stats(self) -> dict:
        semantic = {"total": 0, "vector": 0, "by_state": {}}
        if self._semantic is not None:
            try:
                semantic = self._semantic.stats()
            except Exception as e:
                print(f"[Memory] 语义统计失败: {e}")
        return {
            "archive": self.archive.stats(),
            "index": self.indexer.stats(),
            "jobs": self.jobs.stats(),
            "semantic": semantic,
        }

    # ============ 语义记忆 ============

    def search_semantic(self, query: str, top_k: int = 5,
                        min_similarity: float = 0.4, owner_id: str = "") -> list:
        """检索语义事实，返回 [{"content","metadata","similarity","source":"semantic"}]"""
        if self._semantic is None:
            return []
        try:
            hits = self._semantic.search(query, top_k=top_k,
                                         min_similarity=min_similarity,
                                         owner_id=owner_id)
        except Exception as e:
            print(f"[Memory] 语义检索失败: {e}")
            return []
        out = []
        for h in hits:
            f = h["fact"]
            out.append({
                "content": f.fact,
                "metadata": {
                    "id": f.id,
                    "subject": f.subject,
                    "predicate": f.predicate,
                    "object": f.object,
                    "confidence": f.confidence,
                    "strength": f.strength,
                    "tag": getattr(f, "tag", "misc"),
                    "hits": getattr(f, "hits", 0),
                    "created_at": f.created_at,
                },
                "similarity": h["similarity"],
                "weight": h.get("weight", 0),
                "source": "semantic",
                "_fact": f,   # 内部用，供权重计算
            })
        return out

    def consolidate(self, session_filter: str = None, limit: int = 50,
                    dry_run: bool = False) -> dict:
        """触发情景→语义巩固"""
        if self._semantic is None:
            return {"error": "语义存储未启用"}
        try:
            from backend.memory.consolidator import Consolidator
            c = Consolidator(archive=self.archive, store=self._semantic)
            result = c.consolidate(session_filter=session_filter,
                                   limit=limit, dry_run=dry_run)
            # 巩固后顺便跑一次归档：把衰减到线下的旧事实标记 dormant
            if not dry_run:
                try:
                    archived = self._semantic.reap_archived()
                    result["archived"] = archived
                except Exception as e:
                    print(f"[Memory] 归档失败: {e}")
            return result
        except Exception as e:
            return {"error": str(e)}

    def list_semantic_facts(self, page: int = 1, page_size: int = 20,
                            owner_id: str = "", state: str = "",
                            keyword: str = "") -> dict:
        """分页列出语义事实"""
        if self._semantic is None:
            return {"items": [], "total": 0, "page": 1, "page_size": page_size,
                    "pages": 1, "error": "语义存储未启用"}
        try:
            return self._semantic.list_facts(page, page_size, owner_id, state, keyword)
        except Exception as e:
            return {"items": [], "total": 0, "page": 1, "page_size": page_size,
                    "pages": 1, "error": str(e)}

    def delete_semantic_fact(self, fact_id: str) -> bool:
        if self._semantic is None:
            return False
        return self._semantic.delete(fact_id)

    def clear_semantic(self) -> int:
        if self._semantic is None:
            return 0
        return self._semantic.clear_all()

    # ============ 群聊上下文 ============

    def get_group_context(self, channel_id: str, limit: int = 10) -> list:
        """
        拉取某群最近 N 轮消息，用于被触发时补充上下文。
        返回 [{"speaker": 昵称, "speaker_id": QQ, "text": 内容, "time": 时间, "is_bot": bool}, ...]
        speaker 用昵称而非 QQ 号——模型需要知道"谁在说话"才能理解对话逻辑
        """
        events = self.archive.list_recent_by_channel(channel_id, limit=limit)
        out = []
        for ev in events:
            out.append({
                "speaker": ev.speaker_name or ev.speaker_id,
                "speaker_id": ev.speaker_id,
                "text": ev.user_msg,
                "time": ev.timestamp,
                "is_bot": False,
            })
            # 若该轮有 AI 回复（说明被触发过），也带上
            if ev.ai_msg:
                out.append({
                    "speaker": "Shelwell",
                    "speaker_id": "bot",
                    "text": ev.ai_msg,
                    "time": ev.timestamp,
                    "is_bot": True,
                })
        return out

    # ============ 导入 ============

    def import_jsonl(self, path: str, session_override: str = None,
                     enqueue_index: bool = True) -> dict:
        """
        从外部 JSONL 导入情景记忆。
        兼容旧格式（user/assistant 字段）与新格式（user_msg/ai_msg）。
        返回 {"imported": n, "skipped": n, "errors": n}
        """
        import json as _json
        stats = {"imported": 0, "skipped": 0, "errors": 0}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                        ev = MemoryEvent.from_dict(d)
                        if session_override:
                            ev.session_id = session_override
                        if self.archive.get(ev.id):
                            stats["skipped"] += 1
                            continue
                        if self.archive.append(ev):
                            if self.relationship is not None:
                                try:
                                    self.relationship.observe(
                                        speaker_id=ev.speaker_id,
                                        display_name=ev.speaker_name,
                                        channel_type=ev.channel_type,
                                        channel_id=ev.channel_id,
                                        ts=ev.timestamp)
                                except Exception as e:
                                    print(f"[Memory] 导入时关系等级更新失败: {e}")
                            if enqueue_index:
                                self.jobs.enqueue("index", {"event_id": ev.id},
                                                  dedup_key=ev.id)
                            stats["imported"] += 1
                        else:
                            stats["errors"] += 1
                    except Exception:
                        stats["errors"] += 1
        except Exception as e:
            print(f"[Memory] 导入失败: {e}")
            stats["errors"] += 1
        return stats

    # ============ 删除 ============

    def delete_event(self, event_id: str) -> bool:
        """删除单条情景记忆（归档 + 双索引）"""
        ok = self.archive.delete(event_id)
        self.indexer.delete(event_id)
        return ok

    def delete_session(self, session_id: str) -> int:
        """删除某会话的全部情景记忆，返回删除条数"""
        ids = [ev.id for ev in self.archive.list_by_session(session_id, limit=100000)]
        for eid in ids:
            self.delete_event(eid)
        return len(ids)

    def clear_all(self) -> dict:
        """清空全部情景记忆（归档 + 双索引）"""
        ids = [ev.id for ev in self.archive._iter_all()]
        for eid in ids:
            self.delete_event(eid)
        return {"deleted": len(ids)}

    # ============ 索引任务处理 ============

    def process_index_job(self, job: dict) -> bool:
        """处理一个索引任务，成功返回 True"""
        eid = job["payload"].get("event_id")
        if not eid:
            return True  # 无效任务直接丢弃
        ev = self.archive.get(eid)
        if not ev:
            return True  # 记录已删，跳过
        r = self.indexer.index(ev)
        return r["vector"] or r["lexical"]

    def run_once(self, kind: str = "index") -> int:
        """处理一批到期任务，返回处理数"""
        n = 0
        while True:
            job = self.jobs.claim(kind)
            if not job:
                break
            try:
                ok = self.process_index_job(job) if job["kind"] == "index" else True
                if ok:
                    self.jobs.complete(job["id"])
                else:
                    self.jobs.fail(job["id"], "index returned false")
            except Exception as e:
                self.jobs.fail(job["id"], str(e))
            n += 1
        return n

    # ============ 后台工作线程 ============

    def start_worker(self, interval: float = 5.0):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_flag.clear()

        def loop():
            while not self._stop_flag.is_set():
                try:
                    self.run_once("index")
                except Exception as e:
                    print(f"[Memory] 工作线程异常: {e}")
                try:
                    self._maybe_auto_consolidate()
                except Exception as e:
                    print(f"[Memory] 自动提炼调度异常: {e}")
                self._stop_flag.wait(interval)

        self._worker_thread = threading.Thread(target=loop, daemon=True, name="memory-worker")
        self._worker_thread.start()

    # ---------- 自动提炼（每日定时） ----------

    @staticmethod
    def _auto_settings() -> tuple:
        """读 runtime 配置：返回 (enabled, "HH:MM", limit)"""
        try:
            from backend.runtime_config import get_runtime
            rt = get_runtime()
            enabled = bool(rt.get("memory.auto_consolidate", False))
            hhmm = str(rt.get("memory.consolidate_time", "") or "").strip()
            limit = int(rt.get("memory.consolidate_limit", 50) or 50)
            return enabled, hhmm, limit
        except Exception:
            return False, "", 50

    def _last_auto_date(self) -> str:
        try:
            lines = self._auto_marker.read_text(encoding="utf-8").strip().splitlines()
            return lines[0].strip() if lines else ""
        except Exception:
            return ""

    def auto_due_at(self, now: datetime, last_date: str = "",
                    enabled: bool = None, hhmm: str = None,
                    limit: int = None):
        """
        判断此刻是否该自动提炼。返回 limit 表示该跑，None 表示不跑。
        参数可注入，便于单测；未传时读 runtime。
        """
        if enabled is None or hhmm is None or limit is None:
            e, t, n = self._auto_settings()
            enabled = e if enabled is None else enabled
            hhmm = t if hhmm is None else hhmm
            limit = n if limit is None else limit
        if not enabled or not hhmm:
            return None
        m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", hhmm)
        if not m:
            return None
        target = "%02d:%s" % (int(m.group(1)), m.group(2))
        if now.strftime("%H:%M") != target:
            return None
        if last_date == now.strftime("%Y-%m-%d"):
            return None          # 今天已经跑过
        return limit

    def _maybe_auto_consolidate(self):
        if self._auto_running:
            return
        limit = self.auto_due_at(datetime.now(), last_date=self._last_auto_date())
        if not limit:
            return
        self._auto_running = True
        threading.Thread(target=self._run_auto_consolidate, args=(limit,),
                         daemon=True, name="auto-consolidate").start()

    def _run_auto_consolidate(self, limit: int):
        try:
            r = self.consolidate(limit=limit)
        except Exception as e:
            print(f"[Memory] 自动提炼失败: {e}")
            self._auto_running = False
            return
        if not r.get("error"):
            today = datetime.now().strftime("%Y-%m-%d")
            try:
                self._auto_marker.parent.mkdir(parents=True, exist_ok=True)
                self._auto_marker.write_text(today + "\n", encoding="utf-8")
            except Exception as e:
                print(f"[Memory] 自动提炼标记写入失败: {e}")
        print(f"[Memory] 自动提炼: 处理 {r.get('processed', 0)} 条情景, "
              f"新增 {r.get('facts_new', 0)} 条事实, 失败 {r.get('failed', 0)}")
        self._auto_running = False

    def stop_worker(self):
        self._stop_flag.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=10)

# ============ 场景过滤辅助 ============

def _build_where(session_id, speaker_id, channel_type, channel_id):
    """
    构造 ChromaDB 的 where 条件。
    群聊：channel_id = 群号（看全群对话，保证连贯）
    私聊：speaker_id = 用户号（看此人所有发言，含其群内发言）
    都未指定时回退 session_id。
    """
    if channel_type == "group" and channel_id:
        return {"channel_id": channel_id}
    if speaker_id:
        return {"speaker_id": speaker_id}
    if session_id:
        return {"session_id": session_id}
    return None


def _event_in_scope(ev, session_id, speaker_id, channel_type, channel_id) -> bool:
    """判断事件是否在检索范围内（关键词检索用，与 _build_where 保持一致）"""
    if channel_type == "group" and channel_id:
        return ev.channel_id == channel_id
    if speaker_id:
        return ev.speaker_id == speaker_id
    if session_id:
        return ev.session_id == session_id
    return True

def _collect_results(qres, results, min_similarity, source, weight):
    """把 ChromaDB 查询结果合并进 results，相似度乘 weight"""
    if not qres or not qres.get("ids"):
        return
    for i, doc, meta, dist in zip(
        qres["ids"][0], qres["documents"][0],
        qres["metadatas"][0], qres["distances"][0]):
        sim = (1 - dist) * weight
        if sim < min_similarity:
            continue
        # 同一条记录如果已被更高权重的层命中，保留高分
        if i in results and results[i]["similarity"] >= sim:
            continue
        results[i] = {"content": doc, "metadata": meta,
                      "similarity": round(sim, 3), "source": source}
