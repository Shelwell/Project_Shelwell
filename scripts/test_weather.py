# scripts/test_weather.py
# 天气服务回归：时间档、雪季、持久化、重启固定、温度范围。
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.weather import WeatherService, in_snow_season, time_band  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
STATE = ROOT / "memory" / "weather_test_state.json"


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"  ok {name}")


def main():
    print("[1] 9 档时间")
    expect = {
        0: "深夜", 1: "深夜", 2: "凌晨", 3: "凌晨", 4: "凌晨",
        5: "拂晓", 6: "拂晓", 7: "清晨", 8: "清晨",
        9: "上午", 10: "上午", 11: "上午",
        12: "中午", 13: "中午", 14: "中午",
        15: "下午", 16: "下午", 17: "下午",
        18: "傍晚", 19: "傍晚",
        20: "晚上", 21: "晚上", 22: "晚上",
        23: "深夜",
    }
    for h, name in expect.items():
        check(f"{h:02d}:00 -> {name}", time_band(h) == name)

    print("[2] 雪季边界")
    for d, want in [
        ("2026-12-06", False), ("2026-12-07", True),
        ("2027-01-15", True), ("2027-03-05", True),
        ("2027-03-06", False), ("2026-06-01", False),
    ]:
        day = datetime.strptime(d, "%Y-%m-%d").date()
        check(f"{d} snow={want}", in_snow_season(day) == want)

    print("[3] 同一天重启固定")
    try:
        STATE.unlink()
    except FileNotFoundError:
        pass
    now = datetime(2026, 11, 6, 9, 30, tzinfo=TZ)
    w1 = WeatherService(state_path=str(STATE))
    s1 = w1.current(now)
    w2 = WeatherService(state_path=str(STATE))
    s2 = w2.current(now)
    check("same weather", s1["weather"] == s2["weather"])
    check("same plan", s1["plan"] == s2["plan"])
    check("same temp", s1["temp"] == s2["temp"])

    print("[4] 同一天稍后重启仍固定")
    later = now + timedelta(hours=3)
    w3 = WeatherService(state_path=str(STATE))
    s3 = w3.current(later)
    check("plan unchanged", s3["plan"] == s1["plan"])
    check("slot follows time", s3["slot"] == 12)

    print("[5] 超过一天后进入新计划")
    much_later = now + timedelta(days=3)
    w4 = WeatherService(state_path=str(STATE))
    s4 = w4.current(much_later)
    check("date advanced", s4["date"] == much_later.date().isoformat())
    check("has plan", all(str(h) in s4["plan"] for h in (5, 12, 20)))

    print("[6] 温度范围 / 天气摘要")
    for h in range(24):
        t = datetime(2026, 11, 6, h, 0, tzinfo=TZ)
        s = WeatherService(state_path=str(STATE)).current(t)
        check(f"temp {h:02d} in range", -20 <= s["temp"] <= 35)
    txt = WeatherService(state_path=str(STATE)).summary(s4)
    check("summary weather", "天气" in txt and "°C" in txt)
    env = WeatherService(state_path=str(STATE)).environment(s4)
    check("environment fields", "当前环境" in env and "°C" in env and "｜" in env)

    print("[7] 雪只在雪季")
    for day in [
        datetime(2026, 12, 6, 12, tzinfo=TZ),
        datetime(2026, 12, 7, 12, tzinfo=TZ),
        datetime(2027, 1, 15, 12, tzinfo=TZ),
        datetime(2027, 3, 6, 12, tzinfo=TZ),
        datetime(2026, 7, 1, 12, tzinfo=TZ),
    ]:
        s = WeatherService(state_path=str(STATE)).current(day)
        allowed = all(w not in ("雨夹雪", "小雪", "大雪")
                      or in_snow_season(day.date())
                      for w in s["plan"].values())
        check(f"{day.date()} snow only in season", allowed)
        check(f"{day.date()} has 3 slots",
              all(str(h) in s["plan"] for h in (5, 12, 20)))

    print("[8] 夏季温度不为负")
    for month in (6, 7, 8):
        for day_n in (1, 15, 28):
            d = datetime(2026, month, day_n, 5, 0, tzinfo=TZ)
            sw = WeatherService(state_path=str(STATE))
            sw.current(d)
            sw.current(d + timedelta(hours=7))
            sw.current(d + timedelta(hours=15))
            plan_state = sw._read_state()
            check(f"{d.date()} summer low >= 10", int(plan_state.get("low", -99)) >= 10)
            for h in (5, 12, 20):
                t = sw.current(d.replace(hour=h))
                check(f"{d.date()} {h:02d}:00 temp > 0", t["temp"] > 0)

    print("[9] 跨午夜但未到 05:00 保持前一日计划")
    STATE.unlink(missing_ok=True)
    evening = datetime(2026, 11, 6, 21, 0, tzinfo=TZ)
    e = WeatherService(state_path=str(STATE)).current(evening)
    early = datetime(2026, 11, 7, 0, 30, tzinfo=TZ)
    e2 = WeatherService(state_path=str(STATE)).current(early)
    check("plan kept across midnight", e["plan"] == e2["plan"])
    check("env date advanced", e2["date"] == "2026-11-07")

    print("[10] ContextManager 注入 C1/D")
    from backend.context_manager import ContextManager
    cm = ContextManager(memory_enabled=False)
    out = cm.prepare_context(
        [{"role": "user", "content": "测试"}],
        current_query="测试", speaker_id="u1", channel_type="private")
    text = out[-1]["content"]
    check("C1 不放天气（缓存稳定）", "【天气】" not in out[0]["content"])
    check("D environment", "【当前环境】" in text and "°C" in text)

    STATE.unlink(missing_ok=True)
    print("全部通过")


if __name__ == "__main__":
    main()
