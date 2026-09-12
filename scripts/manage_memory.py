# scripts/manage_memory.py
# 用途：查看、搜索、删除长期记忆
# 用法：
#   python scripts/manage_memory.py list              # 列出所有记忆
#   python scripts/manage_memory.py list 2026-09-11   # 列出指定日期的记忆
#   python scripts/manage_memory.py search "关键词"    # 搜索记忆
#   python scripts/manage_memory.py delete <mem_id>   # 删除指定id的记忆
#   python scripts/manage_memory.py delete-date 2026-09-11  # 删除某天的所有记忆
#   python scripts/manage_memory.py clear             # 清空所有记忆（危险）

import os
import sys
import json
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_DIR = BASE_DIR / "memory" / "history"
CHROMA_DIR = BASE_DIR / "memory" / "vector_store" / "chroma_db"

import chromadb

# ---------- 工具函数 ----------

def get_chroma_collection():
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(name="shelwell_memory")

def load_all_history():
    """读取所有 JSONL 文件，返回 {mem_id: (record, filepath)}"""
    records = {}
    if not HISTORY_DIR.exists():
        return records
    for jsonl_file in sorted(HISTORY_DIR.glob("*.jsonl")):
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    records[rec["id"]] = (rec, jsonl_file)
                except Exception:
                    continue
    return records

# ---------- 命令实现 ----------

def cmd_list(args):
    """列出记忆（可按日期过滤）"""
    records = load_all_history()
    if args.date:
        records = {k: v for k, v in records.items() if v[0]["timestamp"].startswith(args.date)}
    if not records:
        print("没有找到记忆。")
        return
    print(f"\n共 {len(records)} 条记忆：\n" + "=" * 70)
    for mem_id, (rec, _) in sorted(records.items(), key=lambda x: x[1][0]["timestamp"]):
        print(f"\nID: {mem_id}")
        print(f"时间: {rec['timestamp']}")
        print(f"用户: {rec['user'][:60]}")
        print(f"Shelwell: {rec['assistant'][:60]}")
        print("-" * 70)

def cmd_search(args):
    """按关键词搜索记忆（在JSONL里做简单文本匹配）"""
    keyword = args.keyword.lower()
    records = load_all_history()
    matched = {
        k: v for k, v in records.items()
        if keyword in v[0]["user"].lower() or keyword in v[0]["assistant"].lower()
    }
    if not matched:
        print(f"没有找到包含“{args.keyword}”的记忆。")
        return
    print(f"\n找到 {len(matched)} 条匹配：\n" + "=" * 70)
    for mem_id, (rec, _) in matched.items():
        print(f"\nID: {mem_id}")
        print(f"时间: {rec['timestamp']}")
        print(f"用户: {rec['user']}")
        print(f"Shelwell: {rec['assistant']}")
        print("-" * 70)

def cmd_delete(args):
    """删除指定 id 的记忆（同时删 JSONL 和 ChromaDB）"""
    mem_id = args.mem_id
    records = load_all_history()
    if mem_id not in records:
        print(f"未找到 id 为 {mem_id} 的记忆。")
        return

    rec, filepath = records[mem_id]
    print(f"\n即将删除：")
    print(f"  时间: {rec['timestamp']}")
    print(f"  用户: {rec['user']}")
    print(f"  Shelwell: {rec['assistant']}")
    confirm = input("\n确认删除？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    # 1. 从 JSONL 中删除该条
    _remove_from_jsonl(filepath, mem_id)
        
    # 2. 从 ChromaDB 中删除
    try:
        collection = get_chroma_collection()
        collection.delete(ids=[mem_id])
        print(f"已从 ChromaDB 删除 {mem_id}")
    except Exception as e:
        print(f"ChromaDB 删除失败: {e}")

    print("删除完成。")

def cmd_edit(args):
    """编辑指定 id 的记忆（同时更新 JSONL 和 ChromaDB）"""
    mem_id = args.mem_id
    records = load_all_history()
    if mem_id not in records:
        print(f"未找到 id 为 {mem_id} 的记忆。")
        return

    rec, filepath = records[mem_id]
    print(f"\n当前内容：")
    print(f"  时间: {rec['timestamp']}")
    print(f"  用户: {rec['user']}")
    print(f"  Shelwell: {rec['assistant']}")
    print("\n" + "=" * 70)
    print("直接回车保持原内容不变，输入新内容则替换。")
    print("输入 :q 取消编辑。\n")

    # 编辑用户消息
    new_user = input(f"用户消息（当前：{rec['user'][:50]}...）\n> ").strip()
    if new_user == ":q":
        print("已取消。")
        return
    if not new_user:
        new_user = rec["user"]

    # 编辑 Shelwell 回复
    new_assistant = input(f"\nShelwell 回复（当前：{rec['assistant'][:50]}...）\n> ").strip()
    if new_assistant == ":q":
        print("已取消。")
        return
    if not new_assistant:
        new_assistant = rec["assistant"]

    # 确认
    print(f"\n即将更新为：")
    print(f"  用户: {new_user}")
    print(f"  Shelwell: {new_assistant}")
    confirm = input("\n确认保存？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    # 1. 更新 JSONL
    _update_jsonl(filepath, mem_id, new_user, new_assistant)

    # 2. 更新 ChromaDB：先删旧向量，再插入新向量
    try:
        import requests
        collection = get_chroma_collection()

        # 删除旧向量
        collection.delete(ids=[mem_id])

        # 生成新向量（调用 Ollama embedding）
        combined_text = f"用户：{new_user}\nShelwell：{new_assistant}"
        resp = requests.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": combined_text},
            timeout=30
        )
        resp.raise_for_status()
        new_embedding = resp.json()["embedding"]

        # 插入新向量（保留原元数据，更新内容字段）
        old_meta = rec.get("metadata", {})
        new_meta = {
            "timestamp": rec["timestamp"],
            "session_id": rec.get("session_id", "default"),
            "user_msg": new_user[:200],
            "ai_msg": new_assistant[:200],
            "type": "dialogue",
            "entities": old_meta.get("entities", ""),
            "scene": old_meta.get("scene", "")
        }

        collection.add(
            ids=[mem_id],
            embeddings=[new_embedding],
            documents=[combined_text],
            metadatas=[new_meta]
        )
        print(f"\n已更新 ChromaDB 向量")
    except Exception as e:
        print(f"\nChromaDB 更新失败: {e}")
        print("JSONL 已更新，但向量库未同步。请稍后手动处理。")

    print("编辑完成。重启应用后生效。")

def cmd_delete_date(args):
    """删除某一天的所有记忆"""
    date = args.date
    records = load_all_history()
    to_delete = [k for k, v in records.items() if v[0]["timestamp"].startswith(date)]
    if not to_delete:
        print(f"{date} 没有记忆记录。")
        return

    print(f"\n将删除 {date} 的 {len(to_delete)} 条记忆。")
    confirm = input("确认？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    # 从 JSONL 删除（直接处理整个文件）
    jsonl_file = HISTORY_DIR / f"{date}.jsonl"
    if jsonl_file.exists():
        jsonl_file.unlink()
        print(f"已删除文件 {jsonl_file.name}")

    # 从 ChromaDB 批量删除
    try:
        collection = get_chroma_collection()
        collection.delete(ids=to_delete)
        print(f"已从 ChromaDB 删除 {len(to_delete)} 条")
    except Exception as e:
        print(f"ChromaDB 删除失败: {e}")

    print("删除完成。")

def cmd_clear(args):
    """清空所有记忆（危险操作）"""
    records = load_all_history()
    print(f"\n将清空 {len(records)} 条记忆（JSONL + ChromaDB）。")
    confirm = input("此操作不可恢复，输入 YES 确认: ").strip()
    if confirm != "YES":
        print("已取消。")
        return

    # 删除所有 JSONL
    for f in HISTORY_DIR.glob("*.jsonl"):
        f.unlink()
        print(f"已删除 {f.name}")

    # 删除 ChromaDB collection
    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        client.delete_collection("shelwell_memory")
        print("已清空 ChromaDB collection")
    except Exception as e:
        print(f"ChromaDB 清空失败: {e}")

    print("全部记忆已清空。")

def _remove_from_jsonl(filepath, mem_id):
    """从 JSONL 文件中移除指定 id 的行"""
    lines = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec["id"] != mem_id:
                    lines.append(line)
            except Exception:
                lines.append(line)
    with open(filepath, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    print(f"已从 {filepath.name} 删除 {mem_id}")

def _update_jsonl(filepath, mem_id, new_user, new_assistant):
    """更新 JSONL 中指定 id 的记录"""
    lines = []
    updated = False
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec["id"] == mem_id:
                    rec["user"] = new_user
                    rec["assistant"] = new_assistant
                    lines.append(json.dumps(rec, ensure_ascii=False))
                    updated = True
                else:
                    lines.append(line)
            except Exception:
                lines.append(line)
    if updated:
        with open(filepath, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        print(f"已更新 {filepath.name}")

# ---------- 入口 ----------

def main():
    parser = argparse.ArgumentParser(description="Shelwell 记忆管理工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出记忆")
    p_list.add_argument("date", nargs="?", help="可选，指定日期 YYYY-MM-DD")
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", help="搜索记忆")
    p_search.add_argument("keyword", help="关键词")
    p_search.set_defaults(func=cmd_search)

    p_delete = sub.add_parser("delete", help="删除指定 id 的记忆")
    p_delete.add_argument("mem_id", help="记忆 ID")
    p_delete.set_defaults(func=cmd_delete)

    p_edit = sub.add_parser("edit", help="编辑指定 id 的记忆")
    p_edit.add_argument("mem_id", help="记忆 ID")
    p_edit.set_defaults(func=cmd_edit)

    p_delete_date = sub.add_parser("delete-date", help="删除某天的所有记忆")
    p_delete_date.add_argument("date", help="日期 YYYY-MM-DD")
    p_delete_date.set_defaults(func=cmd_delete_date)

    p_clear = sub.add_parser("clear", help="清空所有记忆（危险）")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()