# scripts/test_people_diary.py
# 人物标识清洗 + 日记存储回归。
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.people import PeopleRegistry
from backend.diary import DiaryStore

FAIL = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f" | {detail}"))
    if not cond:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp(prefix="shelwell_people_"))
people_yaml = tmp / "people.yaml"
people_yaml.write_text(
    "people:\n"
    "  - id: P001\n"
    "    qq: '2672768829'\n"
    "    label: '佩奇'\n"
    "    aliases: ['/PR_old/', 'Shelwell']\n"
    "  - id: P002\n"
    "    qq: '1403412613'\n"
    "    label: '安洁莉娜'\n"
    "    aliases: ['安洁']\n",
    encoding="utf-8")

p = PeopleRegistry(path=str(people_yaml))
check("label qq", p.label("2672768829") == "[佩奇]", p.label("2672768829"))
check("label unknown", p.label("999") == "[未知999]", p.label("999"))
check("scrub nickname", "old" not in p.scrub("/PR_old/：你好，Shelwell"),
      p.scrub("/PR_old/：你好，Shelwell"))
check("scrub keeps label", "[佩奇]" in p.scrub("[佩奇] 和 Shelwell"),
      p.scrub("[佩奇] 和 Shelwell"))
check("scrub ascii boundary", p.scrub("xShelwellx") == "xShelwellx",
      p.scrub("xShelwellx"))
check("scrub messages", p.scrub_messages([{"role": "user", "content": "Shelwell"}])[0]["content"] == "[佩奇]")

# 摘要输入/输出清洗（用假 provider，不调模型）
import backend.session_summary as ss
_captured = {}
_orig = ss.provider.chat

def _fake_chat(messages, **kwargs):
    _captured["text"] = messages[-1]["content"]
    return "今天 [佩奇] 问我是否记得他。"

ss.provider.chat = _fake_chat
diary_text = ss.make_diary(
    [{"role": "user", "content": "Shelwell 说：你好"},
     {"role": "assistant", "content": "嗯"}],
    people=p, speaker_label="[佩奇]", is_group=False)
check("summary input no nickname", "Shelwell" not in _captured.get("text", ""),
      _captured.get("text", ""))
check("diary output", "[佩奇]" in diary_text, diary_text)
ss.provider.chat = _orig

diary = DiaryStore(path=str(tmp / "diaries.jsonl"))
diary.append("group:1", "今天 [佩奇] 问我是否记得他。", ["[佩奇]"], "2026-11-07")
diary.append("group:1", "今天 [安洁莉娜] 来了。", ["[安洁莉娜]"], "2026-11-08")
diary.append("private:1", "私聊日记", ["[佩奇]"], "2026-11-08")
check("recent scope", len(diary.recent("group:1", 2)) == 2)
check("scope isolated", len(diary.recent("private:1", 2)) == 1)
check("format", "[安洁莉娜]" in diary.format_recent("group:1", 1), diary.format_recent("group:1", 1))

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("全部通过" if not FAIL else f"失败项: {FAIL}"))
sys.exit(1 if FAIL else 0)
