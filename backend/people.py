# backend/people.py
# 职责：人物标识表。配置文件 -> QQ/别名 -> 固定模型标识 [YY]。
# 设计：昵称只作为 alias，不进入 prompt；prompt 只使用 [YY]。

from __future__ import annotations

import re
from pathlib import Path

import yaml

from backend.config_loader import get


DEFAULT_PATH = "config/people.yaml"


class PeopleRegistry:
    def __init__(self, path: str = None):
        root = Path(__file__).resolve().parent.parent
        p = Path(path or get("people.path", DEFAULT_PATH) or DEFAULT_PATH)
        self.path = p if p.is_absolute() else root / p
        self.people = []
        self.by_qq = {}
        self.by_name = {}
        self.self_label = ""
        self.self_aliases = []
        self.load()

    def load(self):
        self.people = []
        self.by_qq = {}
        self.by_name = {}
        self.self_label = ""
        self.self_aliases = []
        try:
            if not self.path.exists():
                return
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                self_data = data.get("self") or {}
                if isinstance(self_data, dict):
                    self.self_label = str(self_data.get("label") or "").strip().strip("[]")
                    self.self_aliases = [
                        str(x).strip() for x in (self_data.get("aliases") or [])
                        if str(x).strip()]
            rows = data.get("people") if isinstance(data, dict) else []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                qq = str(row.get("qq") or "").strip()
                label = str(row.get("label") or "").strip().strip("[]")
                pid = str(row.get("id") or "").strip() or qq
                if not qq or not label:
                    continue
                # 标签必须是唯一、简短、无括号
                if "[" in label or "]" in label:
                    continue
                aliases = [str(x).strip() for x in (row.get("aliases") or [])
                           if str(x).strip()]
                item = {
                    "id": pid, "qq": qq, "label": label,
                    "aliases": aliases,
                    "notes": str(row.get("notes") or ""),
                }
                self.people.append(item)
                self.by_qq[qq] = item
                self.by_name[label] = item
                for a in aliases:
                    self.by_name.setdefault(a, item)
        except Exception as e:
            print(f"[People] 人物表读取失败: {e}")

    def person(self, qq: str) -> dict:
        return self.by_qq.get(str(qq or "").strip(), {})

    def label(self, qq: str, fallback: str = "") -> str:
        item = self.person(qq)
        if item:
            return "[%s]" % item["label"]
        fb = str(fallback or "").strip().strip("[]")
        if fb:
            return "[%s]" % fb
        tail = str(qq or "").strip()[-4:]
        return "[未知%s]" % tail if tail else "[未知]"

    def label_for_name(self, name: str) -> str:
        item = self.by_name.get(str(name or "").strip())
        if item:
            return "[%s]" % item["label"]
        return ""

    def strip_display_marks(self, text: str) -> str:
        """
        只给 QQ 显示用：去掉已知人物标识的方括号。
        不修改原文，不影响记忆/历史。
        [佩奇]觉得[安洁莉娜]的猫很可爱 -> 佩奇觉得安洁莉娜的猫很可爱
        """
        if not text:
            return text

        known = set(self.by_name.keys())
        if self.self_label:
            known.add(self.self_label)

        def repl(m):
            inner = m.group(1)
            # 已知人物 + 未知人物占位符
            if inner in known or inner.startswith("未知"):
                return inner
            # [笑] / [图片] / [CQ:...] 等不是人物，保留
            return m.group(0)

        return re.sub(r"\[([^\[\]]+)\]", repl, text)

    def self_ref(self) -> str:
        return "[%s]" % self.self_label if self.self_label else ""

    def scrub_self(self, text: str) -> str:
        """只把西尔维娅/希尔薇/Sylvia 归一化；不处理“我”。"""
        if not text or not self.self_label:
            return text
        ref = "[%s]" % self.self_label
        mapping = {}
        patterns = []
        names = [self.self_label] + list(self.self_aliases)
        for name in sorted({x for x in names if x}, key=len, reverse=True):
            if name == ref:
                continue
            mapping[name.lower()] = ref
            if name.isascii():
                patterns.append(
                    r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])")
            else:
                patterns.append(re.escape(name))
        if not patterns:
            return text
        combined = re.compile("|".join(patterns), re.I)
        parts = re.split(r"(\[[^\]]+\])", str(text))
        for i in range(0, len(parts), 2):
            parts[i] = combined.sub(
                lambda m: mapping.get(m.group(0).lower(), m.group(0)), parts[i])
        return "".join(parts)

    def scrub(self, text: str) -> str:

        """把已知昵称/别名/固定称呼统一替换为 [label]；[label] 保持不变。"""

        if not text:

            return text

        mapping = {}

        patterns = []

        for item in self.people:

            label = "[%s]" % item["label"]

            names = [item["label"]] + list(item["aliases"])

            for name in sorted({x for x in names if x}, key=len, reverse=True):

                key = name.lower()

                if key in mapping:

                    continue

                mapping[key] = label

                if name.isascii():

                    pat = r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"

                else:

                    pat = re.escape(name)

                patterns.append(pat)

        if not patterns:

            return text

        combined = re.compile("|".join(patterns), re.I)



        def repl(m):

            return mapping.get(m.group(0).lower(), m.group(0))



        parts = re.split(r"(\[[^\]]+\])", str(text))

        for i in range(0, len(parts), 2):

            parts[i] = combined.sub(repl, parts[i])

        text = "".join(parts)



        def _fix_bracket(m):

            inner = m.group(1)

            item = self.by_name.get(inner)

            return "[%s]" % item["label"] if item else m.group(0)



        return re.sub(r"\[([^\]]+)\]", _fix_bracket, text)



    def scrub_messages(self, messages: list) -> list:
        out = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                out.append(msg)
                continue
            m = dict(msg)
            if "content" in m:
                m["content"] = self.scrub(str(m.get("content") or ""))
            out.append(m)
        return out
