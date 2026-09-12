# scripts/generate_file_tree.py
# 用途：生成 Shelwell 项目的文件树，保存为根目录下的 文件树.md
# 运行：python scripts/generate_file_tree.py

import os
import sys
from pathlib import Path
from datetime import datetime

# ========== 配置 ==========
# 需要忽略的目录（不扫描、不显示）
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "Shelwell_venv",
    "shelwell_train",
    "blobs",
    "manifests",
    "logs",
    "chroma_db",
    ".pytest_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
    "Lib",
    "Scripts"
}

# 忽略所有以 . 开头的隐藏目录（包括 .git 等，上面已列出的也会被覆盖）
# 若希望保留某些隐藏目录，可从 IGNORE_DIRS 中移除，或修改下方判断逻辑
IGNORE_HIDDEN_DIRS = True

# 输出文件名
OUTPUT_FILENAME = "文件树.md"


def should_ignore_dir(dir_name: str) -> bool:
    """判断目录是否应被忽略"""
    if dir_name in IGNORE_DIRS:
        return True
    if IGNORE_HIDDEN_DIRS and dir_name.startswith("."):
        return True
    return False


def build_tree(root_path: Path, current_path: Path = None, prefix: str = "", is_last: bool = True, is_root: bool = False) -> list:
    """
    递归构建文件树，返回字符串行列表
    """
    if current_path is None:
        current_path = root_path

    lines = []
    try:
        entries = sorted(
            current_path.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower())  # 目录在前，文件在后，按名称排序
        )
    except PermissionError:
        return lines

    # 过滤忽略项
    filtered = []
    for entry in entries:
        if entry.is_dir() and should_ignore_dir(entry.name):
            continue
        filtered.append(entry)

    for i, entry in enumerate(filtered):
        is_last_entry = (i == len(filtered) - 1)
        # 构造连接符
        if is_root:
            connector = ""
            new_prefix = ""
        else:
            connector = "└── " if is_last_entry else "├── "
            new_prefix = prefix + ("    " if is_last_entry else "│   ")

        if is_root:
            lines.append(f"{entry.name}/")
        else:
            lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")

        if entry.is_dir():
            # 递归子目录
            sub_lines = build_tree(
                root_path,
                entry,
                new_prefix,
                is_last_entry,
                is_root=False
            )
            lines.extend(sub_lines)

    return lines


def main():
    # 定位项目根目录（脚本所在目录的上一级）
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    if not project_root.exists():
        print(f"错误：项目根目录不存在：{project_root}")
        sys.exit(1)

    print(f"项目根目录: {project_root}")
    print("正在生成文件树...")

    # 生成树形结构
    tree_lines = build_tree(project_root, project_root, is_root=True)

    # 组装 Markdown 内容
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md_lines = [
        "# Shelwell 项目文件树",
        "",
        f"> 生成时间：{now}",
        f"> 项目根目录：`{project_root}`",
        "",
        "```text",
        f"{project_root.name}/",
    ]
    md_lines.extend(tree_lines)
    md_lines.append("```")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("**已忽略的目录**：")
    ignored_list = sorted(IGNORE_DIRS)
    for d in ignored_list:
        md_lines.append(f"- `{d}/`")
    if IGNORE_HIDDEN_DIRS:
        md_lines.append("- 所有以 `.` 开头的隐藏目录")

    content = "\n".join(md_lines)

    # 写入文件
    output_path = project_root / OUTPUT_FILENAME
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"文件树已保存至: {output_path}")
        print(f"共生成 {len(tree_lines)} 行")
    except Exception as e:
        print(f"写入失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()